import os
import tempfile

import numpy as np
import json
import katpoint

from astropy.io import fits
from astropy.modeling import models
from unittest import mock

from katacomb import (make_pbeam_images, make_qa_report,
                      normalise_target_name, organise_qa_output)
from katacomb.aips_export import (_update_target_metadata,
                                  _metadata,
                                  FITS_EXT,
                                  PNG_EXT,
                                  METADATA_JSON)
from katacomb.mock_dataset import MockDataSet

HDR_KEYS = {'NAXIS': 4,
            'NAXIS1': 100,
            'NAXIS2': 100,
            'NAXIS3': 1,
            'NAXIS3': 1,
            'CTYPE1': 'RA---SIN ',
            'CDELT1': -5.13E-04,
            'CRPIX1': 50,
            'CROTA1': 0.000000E+00,
            'CRVAL1': 1.8E+02,
            'CTYPE2': 'DEC--SIN ',
            'CDELT2': 5.13E-04,
            'CRPIX2': 50,
            'CROTA2': 0.00E+00,
            'CRVAL2': -2.23E+01,
            'CTYPE3': 'SPECLNMF ',
            'CDELT3': 5.46E+07,
            'CRPIX3': 1.0E+00,
            'CROTA3': 0.000000E+00,
            'CRVAL3': 8.159335937500E+08,
            'CTYPE4': 'STOKES   ',
            'CDELT4': 1.000000E+00,
            'CRPIX4': 1.000000E+00,
            'CROTA4': 0.000000E+00,
            'CRVAL4': 1.00E+00,
            'NTERM': 2,
            'BUNIT': 'Jy / beam',
            'NSPEC': 1,
            'FREQ0001': 1.284e3,
            'history': 'AIPS   CLEAN BMAJ=  5e-03 BMIN=  1e-03 BPA=  42.11',
            'BMAJ': 5e-3,
            'BMIN': 1e-03,
            'BPA': 42.11
            }


def _create_test_metadata(target_names):
    targets = [katpoint.Target(f'{t}, radec, {100.0 + i}, -35') for
               i, t in enumerate(target_names)]

    target_metadata = {}
    used = []
    for t in targets:
        target = normalise_target_name(t.name, used)
        used.append(target)
        with mock.patch('katacomb.aips_export._integration_time', return_value=80):
            with mock.patch('katacomb.img_facade.ImageFacade', autospec=True) as MockImage:
                MockImage.FArray.RMS = 1e-3
                _update_target_metadata(target_metadata, MockImage, t, target, 'katds', target)

    scans = [('track', 3, targets[0]), ('track', 3, targets[1])]
    ds = MockDataSet(dumps=scans)
    metadata = _metadata(ds, '1234', target_metadata)

    return metadata


def _check_files(outDirName, file_base, suffix):
    outFileName = os.path.join(outDirName, file_base)
    assert os.path.isfile(outFileName + suffix + FITS_EXT)
    assert os.path.isfile(outFileName + suffix + PNG_EXT)
    assert os.path.isfile(outFileName + suffix + '_tnail' + PNG_EXT)

    metadata_file = os.path.join(outDirName, METADATA_JSON)
    assert os.path.isfile(metadata_file)
    return metadata_file


def _check_keys(meta_file, meta_in, suffix, i, name):
    with open(meta_file) as f:
        meta = json.load(f)
        file_base = os.path.splitext(meta_in['FITSImageFilename'][i])[0]
        np.testing.assert_equal(meta['FITSImageFilename'], [file_base + suffix + FITS_EXT])
        np.testing.assert_equal(meta['DecRa'], [f'-35.0,{100.0 + i}'])
        np.testing.assert_equal(meta['ProductType']['ReductionName'], name)


class TestMakePBImages:
    def setup(self):
        # Create temporary directory
        self.tmpdir_in = tempfile.TemporaryDirectory()
        in_array = np.ones([1, 3, 100, 100])
        hdu = fits.PrimaryHDU(in_array)

        for h in HDR_KEYS.keys():
            hdu.header[h] = HDR_KEYS[h]

        # Construct 4 targets
        target_names = ['Gunther Lord of the Gibichungs',
                        'Gutrune', 'Hagen', 'Gutrune']

        self.metadata = _create_test_metadata(target_names)
        # Create input images per target in input directory
        os.mkdir(os.path.join(self.tmpdir_in.name, '1234.writing'))
        for f in self.metadata['FITSImageFilename']:
            inFileName = os.path.join(self.tmpdir_in.name, '1234.writing', f)
            hdu.writeto(inFileName)

    def teardown(self):
        self.tmpdir_in.cleanup()

    def test_make_pb_images(self):

        pipe_dirname = os.path.join(self.tmpdir_in.name, '1234')
        make_pbeam_images(self.metadata, pipe_dirname, '.writing')

        for i in range(4):
            # Expected base name of directories containing new QA products
            outDirName = pipe_dirname + '_' + self.metadata['Targets'][i] + \
                         '_' + self.metadata['Run']
            # Files exist in expected directory
            pb_metadata_file = _check_files(outDirName + '_PB.writing',
                                            self.metadata['Targets'][i], '_PB')

            # Check a sample of the keys are correct
            _check_keys(pb_metadata_file, self.metadata, '_PB', i,
                        'Continuum Image PB corrected')


class TestMakeQAReport:
    def setup(self):
        # create temporary directory
        self.tmpdir_in = tempfile.TemporaryDirectory()

        # Create image data
        in_array = np.zeros([1, 1, 100, 100])
        # Add noise
        np.random.seed(10)
        in_array += np.random.normal(0.0, 1e-5, [1, 1, 100, 100])

        # Add a gaussian source to fit (otherwise QA code will fail)
        y, x = np.mgrid[:100, :100]
        gaus_mod = models.Gaussian2D(0.001, 50, 50, 2.5, 6, 0.6)
        gaus_data = gaus_mod(y, x)

        # Leave in extra axes as QA code will try to drop extra axes
        # inferred from the header
        in_array += gaus_data[np.newaxis, np.newaxis, :, :]

        hdu = fits.PrimaryHDU(in_array)

        for h in HDR_KEYS.keys():
            hdu.header[h] = HDR_KEYS[h]

        # QA code expects a frequency axis
        hdu.header['CTYPE3'] = 'FREQ '
        # Construct 4 targets
        target_names = ['Gunther Lord of the Gibichungs',
                        'Gutrune', 'Hagen', 'Gutrune']

        self.metadata = _create_test_metadata(target_names)
        # Create input images per target
        self.dir_base = []
        for f, t in zip(self.metadata['FITSImageFilename'], self.metadata['Targets']):
            pipe_dirname = os.path.join(self.tmpdir_in.name, '1234')
            # Expected base name of new directories containing QA products
            outDirName = pipe_dirname + '_' + t + '_' + self.metadata['Run']
            self.dir_base.append(outDirName)
            os.mkdir(outDirName + '_PB.writing')

            file_base = os.path.splitext(f)[0]
            inFileName = os.path.join(outDirName + '_PB.writing', file_base + '_PB' + FITS_EXT)
            hdu.writeto(inFileName)

    def teardown(self):
        self.tmpdir_in.cleanup()

    def test_organise_qa_output(self):
        pipe_dirname = os.path.join(self.tmpdir_in.name, '1234')
        # Make the QA report output
        make_qa_report(self.metadata, pipe_dirname, '.writing')

        # Check the QA html file is written in a subdirectory of the PB dir
        qa_suffix = '_PB_continuum_validation_snr5.0_int'
        for dir_base, f in zip(self.dir_base, self.metadata['FITSImageFilename']):
            file_base = os.path.splitext(f)[0]
            outQAName = os.path.join(dir_base + '_PB.writing', file_base + qa_suffix, 'index.html')
            # Report exists in expected directory
            assert os.path.isfile(outQAName)

        # Organise all the QA outputs into separate directories
        organise_qa_output(self.metadata, pipe_dirname, '.writing')

        for i, dir_base in enumerate(self.dir_base):
            f = self.metadata['FITSImageFilename'][i]
            # The .writing tag is removed from PB directory
            file_base = os.path.splitext(f)[0]
            outPBName = os.path.join(dir_base + '_PB', file_base + '_PB' + FITS_EXT)
            assert os.path.isfile(outPBName)

            # RMS and BKG images moved to their own dedicated directories with metadata file
            meta_rms = _check_files(dir_base + '_RMS', file_base, '_PB_aegean_rms')
            _check_keys(meta_rms, self.metadata, '_PB_aegean_rms', i,
                        'Continuum PB Corrected RMS Image')
            meta_bkg = _check_files(dir_base + '_BKG', file_base, '_PB_aegean_bkg')
            _check_keys(meta_bkg, self.metadata, '_PB_aegean_bkg', i,
                        'Continuum PB Corrected Mean Image')

            # QA reports are moved to the own dedicated directories with metadata file
            outQAName = os.path.join(dir_base + '_QA', 'index.html')
            assert os.path.isfile(outQAName)
            assert os.path.isfile(os.path.join(dir_base + '_QA', METADATA_JSON))
