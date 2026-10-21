# Derived MK+ S04 commissioning beam

Created locally on 2026-09-29 from the confidential commissioning inputs.
No beam data were uploaded or published. Original input files were mounted
read-only and are unchanged. This directory is a local derived artifact,
not an upstream or producer-approved beam release.

## Products

- `products/MK+S04_sym_LM.fits`: combined complex full-Jones cube with L/M
  axis labels, suitable for the existing MFBeam splitter.
- `products/MK+S04_sym_native.fits`: identical pixels and numeric coordinates,
  preserving the source PX---SSN/PY---SSN axis labels.
- `products/mfbeam/`: eight split FITS cubes; logical roots are
  `SS_MKPLUS_S04_SYM_Real.fits` and `SS_MKPLUS_S04_SYM_Imag.fits`.
  These SS files do not exist: MFBeam substitutes XX, YY, XY, YX.
- `products/validation.json`: input hashes, interpolation details, checks,
  measured conversion errors, and quantitative join diagnostics.
- `products/frequency_checks.png`: amplitude cuts at three spatial positions.

Frequency grid: 127 planes, 1763671875 Hz to 3486328125 Hz inclusive,
13671875 Hz spacing, copied from `beam_mavg_S04.fits`. Only its frequency
header is used; its beam samples are not mixed into the output.
Spatial grid: S4's 128 x 128 grid, 0.03125 degree spacing, with coordinate
centres -2.0 to +1.96875 degrees on both axes. Upsampling S0 does not
improve its intrinsic spatial resolution.

## Coordinate interpretation

The source headers describe PX---SSN/PY---SSN as a swapped sine projection.
Rather than infer a transpose or sign flip from that wording, this conversion
uses the exact interpretation of the producer-recommended `katbeam.FitsBeam`:

https://github.com/ska-sa/katbeam/blob/f46f11ef7ec361e1d656178d4842fb6450022654/katbeam/fitsbeam.py

`FitsBeam.read_beam_file` reads the first FITS axis as L, the second as M,
using CRVAL, CRPIX and CDELT directly. It does not branch on CTYPE or apply
a spatial swap, reflection or rotation. Its polarization order is VV, HH,
VH, HV. This order and the complex real/imaginary order are preserved.
The existing MFBeam splitter maps these STOKES codes -5,-6,-7,-8 to
XX,YY,XY,YX respectively, with no additional permutation here.

The L/M file adopts this reader convention explicitly, with provenance in
FITS HISTORY. It is **not a general SSN-to-SIN WCS reprojection**. Numerical
agreement with katbeam establishes reader compatibility, not an independent
physical validation of on-sky orientation. The native-label output is retained
so that the interpretation is reversible. No repository converter was changed.

## Interpolation and limitations

S0 is resampled spatially onto S4's grid. Every Jones element is linearly
interpolated in real and imaginary parts on the output frequency grid.
No spatial or frequency extrapolation is performed.

There is no measured overlap: S0 ends at 2621.58203125 MHz and S4 starts
at 2633.544921875 MHz. Output plane index 63 (zero-based), at 2625 MHz,
is a linear combination of those edge planes after spatial resampling.
No gain, phase, polarization alignment, or smoothing correction is fitted.

The edge planes differ measurably. Within radius 1 degree the RMS complex
differences are VV 0.02670, HH 0.02142, VH 0.01717, HV 0.02894, expressed
in units of the normalized central co-polar voltage. These are differences
between distinct frequencies and observations, not estimated interpolation
errors or fractional errors relative to the local beam. The plot includes
cross-polar structure around the join, and structure above 3 GHz inherited
from the input models. The result should be treated as a derived commissioning
model, not a demonstration of physical continuity between the observations.

Mattieu's possible outstanding HV phase correction has **not** been applied.
No correction values were supplied. No antenna-class assignment is inferred
from the MK+ filename; no manifest assigning this model to extension dishes
has been created. These products have not been used for an end-to-end MFBeam
imaging run or baked into a new Docker image.

## Validation

The independent katbeam reader was evaluated at 257 asymmetric spatial grid
points across all 127 output frequencies and all four complex Jones terms.
The maximum absolute difference was 2.984e-8 (float32 rounding scale).
The gap was compared against an explicit weighted sum of the two source
reader outputs. Checks also passed for finite values, central normalization,
native/L-M pixel equality, exact reconstruction from all eight split files,
and FITS checksum/datasum integrity. These are numerical conversion checks,
not polarization calibration or scientific beam validation.

## Reproduce

`combine_beams.py` uses two local reference modules:

- `katbeam_fitsbeam_reference.py`, downloaded from the pinned public commit
  above (its original copyright/license header is retained).
- `beam_conversion_reference.py`, copied unchanged from
  `/scratch2/richarms/git/mfbeam-meerkat-heterogeneous/src/mfbeam_heterogeneous/beam_conversion.py`.

The image used was `harbor.sdp.kat.ac.za/dpp/katsdpcontim:mfbeam-imager-baked`,
local ID `sha256:249c7298f0a307055e2f8dfa36d8e6ca1dda8607637756a30cf41ffa0fd606e9`.
It provided SciPy 1.10.1, Astropy 4.2 and Matplotlib 3.3.4.
Use a fresh output directory; generation intentionally refuses to overwrite
existing full cubes:

```bash
docker run --rm --network none --entrypoint python \
  -e MPLCONFIGDIR=/tmp/matplotlib \
  -v /scratch2/richarms/git/mfbeam-meerkat-heterogeneous/beam-input/commissioning-2026-09-29:/input:ro \
  -v /scratch2/richarms/data/beam-combination-20260929:/work \
  sha256:249c7298f0a307055e2f8dfa36d8e6ca1dda8607637756a30cf41ffa0fd606e9 \
  /work/combine_beams.py /input /work/products-reproduced
```
