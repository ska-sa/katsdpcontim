"""Selectable Obit continuum-imaging backends.

MFImage remains the default backend.  MFBeam is an opt-in backend that takes
one or two externally supplied, manifest-described primary-beam sets.
"""

from contextlib import contextmanager
from dataclasses import dataclass
import logging
import multiprocessing
import os
from pathlib import Path
from shutil import SameFileError, copy2
from tempfile import TemporaryDirectory
from typing import Iterable, Optional, Sequence, Tuple

from astropy.io import fits
import numpy as np
from pretty import pretty
import yaml

from .util import fractional_bandwidth, log_obit_err, task_factory
from .uv_facade import uv_factory


log = logging.getLogger("katacomb")
POLARISATIONS = ("XX", "YY", "XY", "YX")
UV_CLASS = "MFImag"
IMG_CLASS = "IClean"


@dataclass(frozen=True)
class BeamSet:
    """MFBeam filename roots and dish diameter for one antenna cohort.

    MFBeam replaces the first two characters of each root basename with XX,
    YY, XY and YX.  The root itself is therefore logical and need not exist.
    """

    name: str
    real_root: str
    diameter: float
    imag_root: Optional[str] = None

    def __post_init__(self):
        if not self.name:
            raise ValueError("Beam-set name must not be empty")
        if self.diameter <= 0.0:
            raise ValueError("Beam diameter must be positive")
        for root in (self.real_root, self.imag_root):
            if root is not None and len(os.path.basename(root)) < 2:
                raise ValueError("Beam root basename must have at least two characters")


@dataclass(frozen=True)
class BeamManifest:
    """Versioned selection of one or two MFBeam antenna cohorts."""

    model_id: str
    band: str
    beam_sets: Tuple[BeamSet, ...]

    def validate_dataset(self, dataset) -> None:
        """Validate band, antenna diameters and selected frequency coverage."""
        dataset_band = str(dataset.spectral_windows[dataset.spw].band).upper()
        if dataset_band != self.band.upper():
            raise ValueError(
                "MFBeam manifest band {!r} does not match dataset band {!r}".format(
                    self.band, dataset_band
                )
            )

        configured = np.asarray([beam.diameter for beam in self.beam_sets])
        unmatched = []
        for antenna in dataset.ants:
            if not np.any(np.abs(configured - antenna.diameter) < 0.001):
                unmatched.append("{} ({:.6g} m)".format(antenna.name, antenna.diameter))
        if unmatched:
            raise ValueError(
                "Antenna diameters do not match the MFBeam manifest: {}".format(
                    ", ".join(unmatched)
                )
            )

        frequencies = np.asarray(dataset.freqs, dtype=np.float64)
        if frequencies.size:
            requested = (float(np.min(frequencies)), float(np.max(frequencies)))
            for beam in self.beam_sets:
                available = beam_frequency_range(beam.real_root)
                if requested[0] < available[0] or requested[1] > available[1]:
                    raise ValueError(
                        "Selected frequencies {:.6g}-{:.6g} Hz exceed {} beam "
                        "coverage {:.6g}-{:.6g} Hz".format(
                            requested[0], requested[1], beam.name,
                            available[0], available[1]
                        )
                    )


def polarisation_path(root: str, polarisation: str) -> str:
    """Resolve one physical polarisation filename from an MFBeam root."""
    directory, basename = os.path.split(root)
    return os.path.join(directory, polarisation + basename[2:])


def _resolve_root(manifest_dir: Path, value: str) -> str:
    root = Path(value)
    if not root.is_absolute():
        root = manifest_dir / root
    return str(root.resolve())


def load_beam_manifest(filename: str) -> BeamManifest:
    """Load and structurally validate an MFBeam YAML manifest."""
    path = Path(filename).resolve()
    with path.open("r") as stream:
        document = yaml.safe_load(stream)
    if not isinstance(document, dict):
        raise ValueError("MFBeam manifest must contain a YAML mapping")
    if document.get("schema_version") != 1:
        raise ValueError("MFBeam manifest schema_version must be 1")
    model_id = document.get("model_id")
    band = document.get("band")
    entries = document.get("antenna_types")
    if not isinstance(model_id, str) or not model_id:
        raise ValueError("MFBeam manifest model_id must be a non-empty string")
    if not isinstance(band, str) or not band:
        raise ValueError("MFBeam manifest band must be a non-empty string")
    if not isinstance(entries, list) or not 1 <= len(entries) <= 2:
        raise ValueError("MFBeam manifest must define one or two antenna_types")

    beam_sets = []
    for entry in entries:
        if not isinstance(entry, dict):
            raise ValueError("Each MFBeam antenna type must be a mapping")
        try:
            name = entry["name"]
            diameter = float(entry["diameter_m"])
            real_root = _resolve_root(path.parent, entry["real_root"])
        except KeyError as error:
            raise ValueError("Missing MFBeam manifest field {}".format(error.args[0]))
        imag_value = entry.get("imag_root")
        imag_root = None
        if imag_value is not None:
            imag_root = _resolve_root(path.parent, imag_value)
        beam_sets.append(BeamSet(str(name), real_root, diameter, imag_root))

    complex_beams = beam_sets[0].imag_root is not None
    if any((beam.imag_root is not None) != complex_beams for beam in beam_sets):
        raise ValueError("All MFBeam antenna types must consistently include imag_root")
    validate_beam_files(beam_sets)
    return BeamManifest(model_id, band, tuple(beam_sets))


def validate_beam_files(beam_sets: Iterable[BeamSet]) -> None:
    """Check that every physical Jones beam file named by the roots exists."""
    for beam in beam_sets:
        roots = [beam.real_root]
        if beam.imag_root is not None:
            roots.append(beam.imag_root)
        for root in roots:
            for polarisation in POLARISATIONS:
                filename = polarisation_path(root, polarisation)
                if not os.path.isfile(filename):
                    raise FileNotFoundError(filename)


def beam_frequency_range(root: str) -> Tuple[float, float]:
    """Return the inclusive frequency range of one MFBeam Jones cube."""
    filename = polarisation_path(root, "XX")
    header = fits.getheader(filename)
    for axis in range(1, int(header.get("NAXIS", 0)) + 1):
        if str(header.get("CTYPE{}".format(axis), "")).upper().startswith("FREQ"):
            size = int(header["NAXIS{}".format(axis)])
            reference_pixel = float(header.get("CRPIX{}".format(axis), 1.0))
            reference_value = float(header["CRVAL{}".format(axis)])
            increment = float(header["CDELT{}".format(axis)])
            first = reference_value + (1.0 - reference_pixel) * increment
            last = reference_value + (size - reference_pixel) * increment
            return min(first, last), max(first, last)
    raise ValueError("{} has no frequency axis".format(filename))


def stage_beam_sets(beam_sets: Sequence[BeamSet], workdir: str) -> dict:
    """Copy beam files into ``workdir`` and construct MFBeam arguments."""
    if not 1 <= len(beam_sets) <= 2:
        raise ValueError("MFBeam supports one or two antenna beam types")
    validate_beam_files(beam_sets)
    complex_beams = beam_sets[0].imag_root is not None

    for beam in beam_sets:
        roots = [beam.real_root]
        if beam.imag_root is not None:
            roots.append(beam.imag_root)
        for root in roots:
            for polarisation in POLARISATIONS:
                source = polarisation_path(root, polarisation)
                try:
                    copy2(source, workdir)
                except SameFileError:
                    pass

    first = beam_sets[0]
    arguments = {
        "in3DType": "FITS",
        "in3Disk": 0,
        "in3File": os.path.basename(first.real_root),
        "in3Diam": first.diameter,
        "doCmplx": complex_beams,
    }
    if complex_beams:
        arguments.update(
            in4DType="FITS", in4Disk=0,
            in4File=os.path.basename(first.imag_root)
        )
    if len(beam_sets) == 2:
        second = beam_sets[1]
        arguments.update(
            in5DType="FITS", in5Disk=0,
            in5File=os.path.basename(second.real_root),
            in5Diam=second.diameter,
        )
        if complex_beams:
            arguments.update(
                in6DType="FITS", in6Disk=0,
                in6File=os.path.basename(second.imag_root)
            )
    return arguments


@contextmanager
def staged_beam_arguments(beam_sets: Sequence[BeamSet]):
    """Stage beam files in temporary scratch and yield task arguments.

    MFBeam rewrites the first two characters of its input filename, so the
    logical root must be a basename resolved relative to the process working
    directory.  Keep the process in the staging directory while the task runs.
    """
    original_workdir = os.getcwd()
    with TemporaryDirectory(prefix="katacomb-mfbeam-") as workdir:
        arguments = stage_beam_sets(beam_sets, workdir)
        os.chdir(workdir)
        try:
            yield arguments
        finally:
            os.chdir(original_workdir)


class ObitImager:
    """Base class for an Obit task that consumes merged AIPS UV data."""

    name = None
    task_name = None
    copy_an_table = False

    def _arguments(self, uv_path, uv_sources, parameters, prtlv):
        with uv_factory(aips_path=uv_path, mode="r") as uvf:
            merge_desc = uvf.Desc.Dict
        arguments = {}
        arguments.update(uv_path.task_input_kwargs())
        arguments.update(
            uv_path.task_output_kwargs(name="", aclass=IMG_CLASS, seq=0)
        )
        arguments.update(
            uv_path.task_output2_kwargs(name="", aclass=UV_CLASS, seq=0)
        )
        arguments.update(
            maxFBW=fractional_bandwidth(merge_desc) / 20.0,
            nThreads=multiprocessing.cpu_count(),
            prtLv=prtlv,
            Sources=uv_sources,
        )
        arguments.update(parameters)
        return arguments

    def _run_task(self, arguments):
        log.info("%s arguments %s", self.task_name, pretty(arguments))
        task = task_factory(self.task_name, **arguments)
        with log_obit_err(log):
            task.go()

    def run(self, uv_path, uv_sources, parameters, prtlv):
        self._run_task(self._arguments(uv_path, uv_sources, parameters, prtlv))

    def validate_dataset(self, dataset):
        pass


class MFImageImager(ObitImager):
    """The existing homogeneous MFImage task."""

    name = "mfimage"
    task_name = "MFImage"


class MFBeamImager(ObitImager):
    """MFBeam configured with one or two manifest-selected beam cohorts."""

    name = "mfbeam"
    task_name = "MFBeam"
    copy_an_table = True

    def __init__(self, manifest: BeamManifest):
        self.manifest = manifest

    def validate_dataset(self, dataset):
        self.manifest.validate_dataset(dataset)

    def run(self, uv_path, uv_sources, parameters, prtlv):
        arguments = self._arguments(uv_path, uv_sources, parameters, prtlv)
        arguments["doPhase"] = False
        # User-supplied parameters intentionally remain the final override.
        arguments.update(parameters)
        with staged_beam_arguments(self.manifest.beam_sets) as beam_arguments:
            arguments.update(beam_arguments)
            self._run_task(arguments)
