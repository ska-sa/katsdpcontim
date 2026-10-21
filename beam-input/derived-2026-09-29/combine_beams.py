#!/usr/bin/env python3
"""Combine these commissioning S0/S4 cubes, with katbeam-compatible L/M axes.

No antenna-type assignment or HV phase correction is inferred. Inputs are read
only. The output directory must not already contain the generated products.
"""
import argparse
import hashlib
import json
from pathlib import Path

import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import numpy as np
from astropy.io import fits
from scipy.interpolate import RegularGridInterpolator

from beam_conversion_reference import split_beam
from katbeam_fitsbeam_reference import FitsBeam


def axis(h, i):
    return h[f'CRVAL{i}'] + (np.arange(h[f'NAXIS{i}']) + 1 - h[f'CRPIX{i}']) * h[f'CDELT{i}']


def sha256(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load(path):
    with fits.open(path) as hdus:
        h = hdus[0].header.copy()
        a = hdus[0].data.copy()
    assert a.shape[:2] == (2, 4)
    assert [h[f'CTYPE{i}'] for i in range(1, 6)] == [
        'PX---SSN', 'PY---SSN', 'FREQ', 'STOKES', 'COMPLEX']
    np.testing.assert_array_equal(axis(h, 4), [-5, -6, -7, -8])
    np.testing.assert_array_equal(axis(h, 5), [0, 1])
    assert h['CUNIT1'].upper() == h['CUNIT2'].upper() == 'DEG'
    assert h['CUNIT3'].upper() == 'HZ'
    assert all(h.get(f'CROTA{i}', 0) == 0 for i in (1, 2, 3))
    assert not any(k.startswith(('PC1_', 'PC2_', 'CD1_', 'CD2_')) for k in h)
    assert np.isfinite(a).all()
    # (frequency, m, l, polarization), preserving VV, HH, VH, HV.
    j = np.moveaxis(a[0].astype(np.float64) + 1j * a[1], 0, -1)
    grids = (axis(h, 3) / 1e9, axis(h, 2), axis(h, 1))
    return h, grids, RegularGridInterpolator(grids, j, bounds_error=True)


def sample(interp, frequency, mm, ll):
    points = np.column_stack((np.full(mm.size, frequency), mm.ravel(), ll.ravel()))
    return interp(points).reshape(mm.shape + (4,))


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('inputs', type=Path)
    p.add_argument('output', type=Path)
    args = p.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    src0 = args.inputs / 'MK+S0_sym.fits'
    src4 = args.inputs / 'MK+S4_sym.fits'
    template = args.inputs / 'beam_mavg_S04.fits'
    h0, g0, r0 = load(src0)
    h4, g4, r4 = load(src4)
    ht = fits.getheader(template)
    freq = axis(ht, 3) / 1e9
    mm, ll = np.meshgrid(g4[1], g4[2], indexing='ij')
    assert g0[0][-1] < g4[0][0]
    assert freq[0] >= g0[0][0] and freq[-1] <= g4[0][-1]
    assert g4[1][0] >= g0[1][0] and g4[1][-1] <= g0[1][-1]
    assert g4[2][0] >= g0[2][0] and g4[2][-1] <= g0[2][-1]
    lo, hi = g0[0][-1], g4[0][0]
    left = sample(r0, lo, mm, ll)
    right = sample(r4, hi, mm, ll)
    out = np.empty((len(freq),) + mm.shape + (4,), dtype=np.complex128)
    gap_indices = []
    for i, f in enumerate(freq):
        if f <= lo:
            out[i] = sample(r0, f, mm, ll)
        elif f >= hi:
            out[i] = sample(r4, f, mm, ll)
        else:
            gap_indices.append(i)
            w = (f - lo) / (hi - lo)
            out[i] = (1 - w) * left + w * right
    assert len(gap_indices) == 1
    assert np.isfinite(out).all()
    data = np.stack((np.moveaxis(out.real, -1, 0), np.moveaxis(out.imag, -1, 0))).astype(np.float32)
    h = h4.copy()
    for key in ('CHECKSUM', 'DATASUM', 'DATE-MAP', 'DATAMAX', 'DATAMIN'):
        h.pop(key, None)
    for key in ('CRVAL3', 'CRPIX3', 'CDELT3', 'CUNIT3'):
        h[key] = ht[key]
    h['DATAMIN'] = float(data.min())
    h['DATAMAX'] = float(data.max())
    h['BAND'] = 'S'
    h['ORIGIN'] = 'Local derived commissioning beam; see validation.json'
    h['DATE'] = '2026-09-29'
    h.add_history('Derived from MK+S0_sym.fits and MK+S4_sym.fits; not a new measurement.')
    h.add_history('S0 spatially resampled onto S4 grid; linear complex Jones interpolation.')
    h.add_history('Frequency grid copied from beam_mavg_S04.fits: 127 planes.')
    h.add_history('Gap 2621.58203125--2633.544921875 MHz linearly interpolated.')
    h.add_history('Gap output plane: 2625 MHz (zero-based index 63). No extrapolation.')
    h.add_history('VV,HH,VH,HV order retained. No HV phase correction applied.')
    for name, src in [('S0', src0), ('S4', src4)]:
        h.add_history(f'{name} SHA256 {sha256(src)}')
    native_path = args.output / 'MK+S04_sym_native.fits'
    fits.PrimaryHDU(data, h).writeto(native_path, checksum=True)
    # This is an explicit adoption of FitsBeam's interpretation, not a general
    # WCS transformation from arbitrary SSN data. Keep native output above.
    h['CTYPE1'] = ('L----SIN', 'L as interpreted by katbeam FitsBeam')
    h['CTYPE2'] = ('M----SIN', 'M as interpreted by katbeam FitsBeam')
    h.add_history('L/M interpretation follows katbeam FitsBeam.read_beam_file at commit')
    h.add_history('f46f11ef7ec361e1d656178d4842fb6450022654: axis1=L, axis2=M.')
    h.add_history('No spatial transpose, reflection, rotation or polarization permutation.')
    lm_path = args.output / 'MK+S04_sym_LM.fits'
    fits.PrimaryHDU(data, h).writeto(lm_path, checksum=True)

    # Independent producer-reader comparison at asymmetric spatial points,
    # every output frequency, and every Jones term. This catches axis flips,
    # component swaps, polarization permutations and frequency offsets.
    fb0, fb4, fbo = (FitsBeam(str(x)) for x in (src0, src4, lm_path))
    rng = np.random.RandomState(20260929)
    iy, ix = rng.randint(0, 128, size=(2, 257))
    test_l, test_m = ll[iy, ix], mm[iy, ix]
    errors = {}
    for pol in ('VV', 'HH', 'VH', 'HV'):
        error = 0.0
        for f in freq:
            if f <= lo:
                expected = getattr(fb0, pol)(test_l, test_m, f * 1000)
            elif f >= hi:
                expected = getattr(fb4, pol)(test_l, test_m, f * 1000)
            else:
                w = (f - lo) / (hi - lo)
                expected = ((1 - w) * getattr(fb0, pol)(test_l, test_m, lo * 1000)
                            + w * getattr(fb4, pol)(test_l, test_m, hi * 1000))
            actual = getattr(fbo, pol)(test_l, test_m, f * 1000)
            error = max(error, float(np.max(np.abs(expected - actual))))
        assert error < 2e-7, (pol, error)
        errors[pol] = error
    np.testing.assert_allclose(data[0, :2, :, 64, 64], 1, atol=2e-7, rtol=0)
    np.testing.assert_allclose(data[1, :, :, 64, 64], 0, atol=1e-7, rtol=0)
    np.testing.assert_allclose(data[:, 2:, :, 64, 64], 0, atol=1e-7, rtol=0)
    # Native and normalized forms carry identical pixels and numerical axes.
    with fits.open(native_path) as a, fits.open(lm_path) as b:
        np.testing.assert_array_equal(a[0].data, b[0].data)
        for i in range(1, 6):
            np.testing.assert_array_equal(axis(a[0].header, i), axis(b[0].header, i))

    split_dir = args.output / 'mfbeam'
    split_beam(lm_path, split_dir, 'MKPLUS_S04_SYM')
    for ci, component in enumerate(('Real', 'Imag')):
        for pi, pol in enumerate(('XX', 'YY', 'XY', 'YX')):
            path = split_dir / f'{pol}_MKPLUS_S04_SYM_{component}.fits'
            with fits.open(path, checksum=True) as hs:
                np.testing.assert_array_equal(hs[0].data, data[ci, pi:pi+1])
                assert hs[0].header['CRVAL4'] == -5 - pi
                assert hs[0].verify_checksum() == hs[0].verify_datasum() == 1
                # The reference splitter copies aggregate min/max; correct per-file.
            with fits.open(path, mode='update') as hs:
                hs[0].header['DATAMIN'] = float(hs[0].data.min())
                hs[0].header['DATAMAX'] = float(hs[0].data.max())
                hs[0].add_checksum()
    for path in (native_path, lm_path, *sorted(split_dir.glob('*.fits'))):
        with fits.open(path, checksum=True) as hs:
            hs.verify('exception')
            assert hs[0].verify_checksum() == hs[0].verify_datasum() == 1
    core = np.hypot(ll, mm) <= 1.0
    # Differences across the gap include actual frequency evolution as well as
    # possible differences between observations; they are not error estimates.
    join = {}
    for pi, pol in enumerate(('VV', 'HH', 'VH', 'HV')):
        delta = np.abs((right - left)[..., pi][core])
        join[pol] = {'rms_complex_difference_r_le_1deg': float(np.sqrt(np.mean(delta**2))),
                     'max_complex_difference_r_le_1deg': float(delta.max())}
    report = {
        'katbeam_commit': 'f46f11ef7ec361e1d656178d4842fb6450022654',
        'input_sha256': {p.name: sha256(p) for p in (src0, src4, template)},
        'frequency_Hz': {'first': freq[0]*1e9, 'last': freq[-1]*1e9,
                         'step': ht['CDELT3'], 'planes': len(freq)},
        'spatial_grid': {'shape': list(mm.shape), 'step_deg': h4['CDELT1'],
                         'l_range_deg': [float(ll.min()), float(ll.max())],
                         'm_range_deg': [float(mm.min()), float(mm.max())]},
        'interpolated_gap': {'input_edges_Hz': [lo*1e9, hi*1e9],
                             'output_indices_zero_based': gap_indices,
                             'output_frequencies_Hz': [float(freq[i]*1e9) for i in gap_indices]},
        'katbeam_max_complex_absolute_error': errors,
        'join_diagnostics': join,
        'checks': ['all values finite', 'no spatial or spectral extrapolation',
                   'katbeam comparisons: all 127 planes, four Jones terms, 257 asymmetric points',
                   'unity central co-polar response and zero central cross-polar response',
                   'native and LM pixels identical', 'split files exactly reconstruct LM pixels',
                   'FITS checksums and datasums pass'],
        'limitations': ['SSN labels interpreted as L/M exactly as katbeam reader does; not a general WCS transform',
                        'gap interpolated without overlapping measurements',
                        'possible producer-mentioned HV phase correction not applied',
                        'antenna class not inferred from MK+ filename; no extension assignment made'],
    }
    (args.output / 'validation.json').write_text(json.dumps(report, indent=2) + '\n')
    fig, axes = plt.subplots(2, 2, figsize=(11, 8))
    for pi, (pol, ax) in enumerate(zip(('VV', 'HH', 'VH', 'HV'), axes.ravel())):
        for x, y in [(64, 64), (80, 64), (88, 80)]:
            ax.plot(freq, np.abs(out[:, y, x, pi]), label=f'L={ll[y,x]:g}, M={mm[y,x]:g} deg')
        ax.axvspan(lo, hi, color='grey', alpha=.3)
        ax.set(title=pol, xlabel='Frequency (GHz)', ylabel='Jones amplitude')
        ax.legend(fontsize=7)
        ax.grid(alpha=.3)
    fig.suptitle('Derived S04 beam: grey interval is interpolated between separate observations')
    fig.tight_layout()
    fig.savefig(args.output / 'frequency_checks.png', dpi=150)
    plt.close(fig)
    print(json.dumps(report, indent=2))


if __name__ == '__main__':
    main()
