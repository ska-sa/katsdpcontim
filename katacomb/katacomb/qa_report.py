import copy
import json
import logging
import os
import shlex
import sys

import contextlib
import katpoint
import numpy as np
from astropy.io import fits


import Radio_continuum_validation as rcv
import katsdpimageutils.primary_beam_correction as pbc
from katacomb.aips_export import _make_pngs, FITS_EXT, PNG_EXT, METADATA_JSON

log = logging.getLogger('katacomb')


@contextlib.contextmanager
def log_qa(logger):
    """Trap QA stdout and stderr messages and send them to logger."""
    original = sys.stdout
    original_err = sys.stderr
    sl = StreamToLogger(logger, logging.INFO)
    sys.stdout = sl

    sl = StreamToLogger(logger, logging.ERROR)
    sys.stderr = sl

    yield
    sys.stdout = original
    sys.stderr = original_err


def make_pbeam_images(metadata, in_dir, out_dir):
    """Write primary beam corrected images.

    Make a single plane FITS image, a PNG and a thumbnail
    per target. Write a new metadata file.
    Parameters
    ----------
    metadata : dict
        dictionary containing pipeline metadata
    in_dir : str
        path containing pipeline output files
    out_dir : str
        path to write primary beam corrected images to
    """
    filenames = metadata['FITSImageFilename']
    targets = metadata['KatpointTargets']
    for target, out_file in zip(targets, filenames):
        target = katpoint.Target(target)
        out_filebase = os.path.splitext(out_file)[0]
        out_filebase_pb = out_filebase + '_PB'
        log.info('Write primary beam corrected FITS output: %s',
                 out_filebase_pb + FITS_EXT)

        in_path = os.path.join(in_dir, out_file)
        pbc_path = os.path.join(out_dir, out_filebase_pb + FITS_EXT)
        bp, raw_image = pbc.beam_pattern(in_path)
        pbc_image = pbc.primary_beam_correction(bp, raw_image, px_cut=0.1)
        pbc.write_new_fits(pbc_image, in_path, outputFilename=pbc_path)

        log.info('Write primary beam corrected PNG output: %s',
                 out_filebase_pb + PNG_EXT)
        _caption_pngs(out_dir, out_filebase_pb,
                      target, 'PB Corrected')

    make_image_metadata(metadata, '_PB', out_dir,
                        'Continuum Image PB corrected',
                        'Continuum image PB corrected')


def make_qa_report(metadata, pb_dir):
    """Write the QA report.

    Parameters
    ---------
    metadata : dict
        dictionary containing pipeline metadata
    pb_dir : str
        path containing primary beam corrected images
    """
    # Change directory as QA code writes output directly to the running directory
    work_dir = os.getcwd()
    os.chdir(pb_dir)

    filenames = metadata['FITSImageFilename']
    for fits_file in filenames:
        pb_filebase = os.path.splitext(fits_file)[0] + '_PB'
        log.info('Write QA report output')

        pb_fits = os.path.join(pb_dir, pb_filebase + FITS_EXT)
        command = '/home/kat/valid/Radio_continuum_validation -I {} --telescope MeerKAT -F'\
                  ' /home/kat/valid/filter_config_MeerKAT.txt -r'.format(pb_fits)
        sysarg = shlex.split(command)
        with log_qa(log):
            rcv.main(sysarg[0], sysarg[1:])
        os.chdir(pb_dir)
    os.chdir(work_dir)


def organise_qa_output(metadata, pb_dir, qa_dir, rms_dir, bkg_dir):
    """Organise QA output into separate directories.

    Move the rms and mean images created by the QA report code to
    dedicated per image type directories. Create metadata files per
    image type directory. Move each QA report directory to
    a given directory, write a metadata file in each individual report
    directory.
    Parameters
    ----------
    metadata : dict
        dictionary containing pipeline metadata
    pb_dir : str
        path containing primary beam corrected images and QA output
    qa_dir : str
        path where qa report will be moved
    rms_dir : str
        path where rms images will be moved
    bkg_dir : str
        path where bkg images will be moved
    """
    filenames = metadata['FITSImageFilename']
    targets = metadata['KatpointTargets']
    for target, fits_file in zip(targets, filenames):
        target = katpoint.Target(target)

        pb_filebase = os.path.splitext(fits_file)[0] + '_PB'
        qa_report = pb_filebase + '_continuum_validation_snr5.0_int'
        os.mkdir(os.path.join(qa_dir, qa_report))
        os.rename(os.path.join(pb_dir, qa_report), os.path.join(qa_dir, qa_report))
        make_report_metadata(metadata, os.path.join(qa_dir, qa_report))

        rms_image = pb_filebase + '_aegean_rms'
        os.rename(os.path.join(pb_dir, rms_image + FITS_EXT),
                  os.path.join(rms_dir, rms_image + FITS_EXT))
        _add_missing_axes(os.path.join(rms_dir, rms_image + FITS_EXT))
        _caption_pngs(rms_dir, rms_image, target, 'RMS PB Corrected')

        bkg_image = pb_filebase + '_aegean_bkg'
        os.rename(os.path.join(pb_dir, bkg_image + FITS_EXT),
                  os.path.join(bkg_dir, bkg_image + FITS_EXT))
        _add_missing_axes(os.path.join(bkg_dir, bkg_image + FITS_EXT))
        _caption_pngs(bkg_dir, bkg_image, target, 'MEAN PB Corrected')

    make_image_metadata(metadata, '_aegean_rms', rms_dir,
                        'Continuum PB Corrected RMS Image',
                        'Continuum PB Corrected RMS image')
    make_image_metadata(metadata, '_aegean_bkg', bkg_dir,
                        'Continuum PB Corrected Mean Image',
                        'Continuum PB Corrected Mean image')


def _caption_pngs(in_dir, fits_file, target, label):
    """Caption and make PNG files"""
    imghdr = fits.open(os.path.join(in_dir, fits_file + FITS_EXT))[0].header
    center_freq = imghdr['CRVAL3'] / 1e6
    caption = f'{target.name} Continuum ({center_freq:.0f} MHz) ({label})'
    _make_pngs(in_dir, fits_file, caption)


def _add_missing_axes(fitsimage):
    """Aegean strips off the first two axes of fits image, restore them"""
    image = fits.open(fitsimage)
    hdr = image[0].header
    data = image[0].data

    new_hdu = fits.PrimaryHDU(data[np.newaxis, np.newaxis, :, :], hdr)
    new_hdu.writeto(fitsimage, overwrite=True)


def make_image_metadata(metadata, suffix, outdir, rname, desc):
    """Write a new image metadata file, based on the original pipeline metadata.

    Parameters
    ----------
    suffix : str
        suffix added to the FITS and PNG image file names
    outdir : str
        path to write metadata file to
    rname : str
        'ReductionName' value in new metadata file
    desc : str
        'Description' value in new metdata file
    """
    meta_suffix = copy.deepcopy(metadata)
    filenames = metadata['FITSImageFilename']
    for i, out_file in enumerate(filenames):
        out_filebase = os.path.splitext(out_file)[0]
        out_filebase_suffix = out_filebase + suffix
        _update_metadata_imagenames(meta_suffix, out_filebase_suffix, i)

    meta_suffix['ProductType']['ReductionName'] = rname
    desc_prefix = meta_suffix['Description'].split(':')[0]
    meta_suffix['Description'] = desc_prefix + ': Continuum image PB corrected'

    write_metadata(meta_suffix, outdir)


def make_report_metadata(metadata, out_dir):
    """Write a new reduction product metadata file.

    It is based on the original pipeline metadata, but with
    updated 'Description' and 'ReductionName' keys.
    Parameters
    ----------
    metadata : dict
        dictionary containing pipeline metadata
    out_dir : str
        path to write metadata file to
    """
    metadata_qa = {}
    # Edit the product type and description
    metadata_qa["ProductType"] = {"ProductTypeName": "MeerKATReductionProduct",
                                  "ReductionName": "Continuum Image Quality Report"}

    desc_prefix = metadata["Description"].split(':')[0]
    metadata_qa["Description"] = desc_prefix + ": Continuum image quality report"

    # Copy remaining keys from original metadata
    report_keys = ["StartTime", "CaptureBlockId",
                   "ProposalId", "Observer", "ScheduleBlockIdCode",
                   "Run"]
    for key in report_keys:
        metadata_qa[key] = metadata[key]
    write_metadata(metadata_qa, out_dir)


def _update_metadata_imagenames(metadata, out_filebase, i):
    """Update the filenames in the metadata dictionary"""
    metadata['FITSImageFilename'][i] = out_filebase + FITS_EXT
    metadata['PNGImageFileName'][i] = out_filebase + PNG_EXT
    metadata['PNGThumbNailFileName'][i] = out_filebase + '_tnail' + FITS_EXT


def write_metadata(metadata, out_dir):
    """Write metadata file to out_dir"""
    try:
        metadata_file = os.path.join(out_dir, METADATA_JSON)
        log.info('Write metadata JSON: %s', metadata_file)
        with open(metadata_file, 'w') as meta:
            json.dump(metadata, meta)
    except Exception as e:
        log.warn("Creation of %s failed.\n%s", metadata_file, str(e))


class StreamToLogger:
    """
    Fake file-like stream object that redirects writes to a logger instance.
    """
    def __init__(self, logger, log_level=logging.INFO):
        self.logger = logger
        self.log_level = log_level
        self.linebuf = ''

    def write(self, buf):
        for line in buf.rstrip().splitlines():
            # Remove the process id from Aegean log messages, they have the format
            # process : level message
            line = line.rstrip().split(':')[-1]
            self.logger.log(self.log_level, line)

    def flush(self):
        pass
