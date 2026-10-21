#!/usr/bin/env python3
"""Split a complex full-Jones beam cube into the files MFBeam expects.

The 2026 MeerKAT beam products contain axes L, M, frequency, Stokes and
complex component in one FITS primary HDU.  Obit's MFBeam instead discovers
four polarisation files by replacing the first two characters of a supplied
filename root (``SS``) with ``XX``, ``YY``, ``XY`` and ``YX``.  It takes a
second root when the imaginary component is present.
"""

import argparse
from pathlib import Path
from typing import Dict, Iterable, Tuple

import numpy as np
from astropy.io import fits


STOKES_NAMES = {
    -5: "XX",
    -6: "YY",
    -7: "XY",
    -8: "YX",
}


def _axis_values(header: fits.Header, axis: int) -> Iterable[int]:
    """Return the integer coordinate value of every pixel on an axis."""
    size = header[f"NAXIS{axis}"]
    reference_pixel = header.get(f"CRPIX{axis}", 1.0)
    reference_value = header[f"CRVAL{axis}"]
    increment = header.get(f"CDELT{axis}", 1.0)
    for pixel in range(1, size + 1):
        yield int(round(reference_value + (pixel - reference_pixel) * increment))


def _output_header(source: fits.Header, stokes_code: int) -> fits.Header:
    """Make a four-axis header for one Stokes and complex plane."""
    header = source.copy()
    for prefix in ("NAXIS", "CTYPE", "CRVAL", "CRPIX", "CDELT", "CROTA", "CUNIT"):
        key = f"{prefix}5"
        if key in header:
            del header[key]
    # PC/CD matrix terms involving the removed axis are unusual in these
    # products, but remove them if a future product adds them.
    for key in list(header):
        if (key.startswith("PC") or key.startswith("CD")) and "_5" in key:
            del header[key]
        elif (key.startswith("PC5_") or key.startswith("CD5_")):
            del header[key]
    header["NAXIS"] = 4
    header["NAXIS4"] = 1
    header["CRPIX4"] = 1.0
    header["CRVAL4"] = stokes_code
    header["CDELT4"] = 1.0
    header.add_history("Split from a complex full-Jones cube for use by Obit MFBeam")
    return header


def split_beam(input_path: Path, output_dir: Path, label: str) -> Tuple[Path, Path]:
    """Split one input cube and return its MFBeam real and imaginary roots."""
    with fits.open(input_path, memmap=True) as hdul:
        source = hdul[0]
        header = source.header
        if header.get("NAXIS") != 5:
            raise ValueError(f"{input_path}: expected a 5-D primary array")
        expected_axes = ("L", "M", "FREQ", "STOKES", "COMPLEX")
        actual_axes = tuple(str(header.get(f"CTYPE{i}", "")).upper() for i in range(1, 6))
        for expected, actual in zip(expected_axes, actual_axes):
            if not actual.startswith(expected):
                raise ValueError(
                    f"{input_path}: expected axes {expected_axes}, got {actual_axes}"
                )
        if source.data.shape[0] != 2:
            raise ValueError(f"{input_path}: COMPLEX axis must have real and imaginary planes")

        stokes_by_index: Dict[int, str] = {}
        for index, code in enumerate(_axis_values(header, 4)):
            try:
                stokes_by_index[index] = STOKES_NAMES[code]
            except KeyError as exc:
                raise ValueError(f"{input_path}: unsupported Stokes code {code}") from exc
        if set(stokes_by_index.values()) != set(STOKES_NAMES.values()):
            raise ValueError(f"{input_path}: expected exactly XX, YY, XY and YX")

        output_dir.mkdir(parents=True, exist_ok=True)
        for complex_index, component in enumerate(("Real", "Imag")):
            for stokes_index, polarisation in stokes_by_index.items():
                output_path = output_dir / f"{polarisation}_{label}_{component}.fits"
                # FITS data are returned in reverse axis order: COMPLEX,
                # STOKES, FREQ, M, L.  Keep a singleton Stokes axis.
                data = np.asarray(
                    source.data[complex_index, stokes_index : stokes_index + 1],
                    dtype=np.float32,
                )
                fits.PrimaryHDU(
                    data=data,
                    header=_output_header(header, next(
                        code for code, name in STOKES_NAMES.items() if name == polarisation
                    )),
                ).writeto(output_path, overwrite=True, checksum=True)

    return (
        output_dir / f"SS_{label}_Real.fits",
        output_dir / f"SS_{label}_Imag.fits",
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input", type=Path, help="Input full-Jones FITS cube")
    parser.add_argument("output_dir", type=Path, help="Directory for the eight MFBeam files")
    parser.add_argument(
        "--label",
        help="Filename label (default: input filename without 'beam_' and '.fits')",
    )
    args = parser.parse_args()
    default_label = args.input.stem
    if default_label.startswith("beam_"):
        default_label = default_label[len("beam_") :]
    label = args.label or default_label.upper()
    real_root, imag_root = split_beam(args.input, args.output_dir, label)
    print(f"real root: {real_root}")
    print(f"imag root: {imag_root}")


if __name__ == "__main__":
    main()
