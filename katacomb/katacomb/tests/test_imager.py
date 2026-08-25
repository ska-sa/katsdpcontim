from types import SimpleNamespace

from astropy.io import fits
import numpy as np
import pytest

from katacomb.imager import (
    MFBeamImager,
    MFImageImager,
    load_beam_manifest,
    polarisation_path,
    stage_beam_sets,
)


def _write_beam_files(directory, label):
    for component in ("Real", "Imag"):
        for polarisation in ("XX", "YY", "XY", "YX"):
            path = directory / "{}_{}_{}.fits".format(
                polarisation, label, component
            )
            hdu = fits.PrimaryHDU(np.zeros((3, 2, 2), dtype=np.float32))
            hdu.header["CTYPE3"] = "FREQ"
            hdu.header["CRPIX3"] = 1.0
            hdu.header["CRVAL3"] = 1.0e9
            hdu.header["CDELT3"] = 1.0e8
            hdu.writeto(path)


def _write_manifest(tmp_path):
    _write_beam_files(tmp_path, "MK")
    _write_beam_files(tmp_path, "MKE")
    manifest = tmp_path / "beams.yaml"
    manifest.write_text(
        """\
schema_version: 1
model_id: test-mk-mke
band: L
antenna_types:
  - name: MK
    diameter_m: 13.5
    real_root: SS_MK_Real.fits
    imag_root: SS_MK_Imag.fits
  - name: MKE
    diameter_m: 15.0
    real_root: SS_MKE_Real.fits
    imag_root: SS_MKE_Imag.fits
"""
    )
    return manifest


def test_polarisation_path():
    assert polarisation_path("models/SS_demo.fits", "XY") == \
        "models/XY_demo.fits"


def test_load_and_stage_two_complex_beams(tmp_path):
    manifest = load_beam_manifest(str(_write_manifest(tmp_path)))
    assert manifest.model_id == "test-mk-mke"
    assert len(manifest.beam_sets) == 2
    assert manifest.beam_sets[1].diameter == 15.0

    workdir = tmp_path / "work"
    workdir.mkdir()
    arguments = stage_beam_sets(manifest.beam_sets, str(workdir))
    assert arguments["doCmplx"] is True
    assert arguments["in3Diam"] == 13.5
    assert arguments["in5Diam"] == 15.0
    assert len(list(workdir.glob("*.fits"))) == 16


def test_manifest_validates_selected_dataset(tmp_path):
    manifest = load_beam_manifest(str(_write_manifest(tmp_path)))
    dataset = SimpleNamespace(
        spectral_windows=[SimpleNamespace(band="L")],
        spw=0,
        ants=[
            SimpleNamespace(name="m000", diameter=13.5),
            SimpleNamespace(name="e116", diameter=15.0),
        ],
        freqs=np.asarray([1.05e9, 1.15e9]),
    )
    manifest.validate_dataset(dataset)

    dataset.ants.append(SimpleNamespace(name="unknown", diameter=14.0))
    with pytest.raises(ValueError, match="unknown"):
        manifest.validate_dataset(dataset)


def test_manifest_rejects_missing_physical_file(tmp_path):
    manifest_path = _write_manifest(tmp_path)
    (tmp_path / "YX_MKE_Imag.fits").unlink()
    with pytest.raises(FileNotFoundError, match="YX_MKE_Imag"):
        load_beam_manifest(str(manifest_path))


def test_imager_selection_is_explicit(tmp_path):
    manifest = load_beam_manifest(str(_write_manifest(tmp_path)))
    assert MFImageImager().task_name == "MFImage"
    assert MFBeamImager(manifest).task_name == "MFBeam"
    assert MFBeamImager(manifest).copy_an_table is True
