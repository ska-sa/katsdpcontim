"""Check packaged S-band beam integrity and optionally real dataset coverage."""
import argparse
from pathlib import Path
from tempfile import TemporaryDirectory

from astropy.io import fits
import numpy as np
import katacomb
from katacomb.imager import load_beam_manifest, stage_beam_sets


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--dataset')
    args = parser.parse_args()
    directory = (Path(katacomb.__file__).parent / 'conf' / 'beam-models'
                 / 's-commissioning-2026-09-29')
    files = sorted(directory.glob('*.fits'))
    assert len(files) == 16
    for path in files:
        with fits.open(path, checksum=True) as hdus:
            hdu = hdus[0]
            hdus.verify('exception')
            assert hdu.verify_checksum() == hdu.verify_datasum() == 1
            assert np.isfinite(hdu.data).all()
            assert hdu.header['CTYPE1'] == 'L----SIN'
            assert hdu.header['CTYPE2'] == 'M----SIN'
            assert hdu.header['NAXIS3'] == 127
            assert hdu.header['CRVAL3'] == 1763671875.0
            assert hdu.header['CDELT3'] == 13671875.0
    manifest = load_beam_manifest(str(directory / 'manifest.yaml'))
    assert manifest.band == 'S'
    assert [b.diameter for b in manifest.beam_sets] == [13.5, 15.0]
    with TemporaryDirectory() as staging:
        params = stage_beam_sets(manifest.beam_sets, staging)
        assert params['in3Diam'] == 13.5 and params['in5Diam'] == 15.0
        assert params['doCmplx']
        assert len(list(Path(staging).glob('*.fits'))) == 16
    print('PASS: 16 S-band FITS files, checksums, axes, manifest and staging')
    if args.dataset:
        import katdal
        ds = katdal.open(args.dataset, stream_name='sdp_l0')
        ds.select(scans='track', corrprods='cross', pol=['HH', 'VV'],
                  targets='J0413-8000', channels=slice(64, 4032))
        manifest.validate_dataset(ds)
        print('PASS: actual S1 dataset band, antenna diameters and selected frequencies')
        print('Selected frequency range: {}--{} Hz'.format(min(ds.freqs), max(ds.freqs)))
        print('Antenna count: {}'.format(len(ds.ants)))


if __name__ == '__main__':
    main()
