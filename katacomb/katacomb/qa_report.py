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


def _productdir(metadata, base_dir, i, suffix, write_tag):
    target_name = metadata['Targets'][i]
    run = metadata['Run']
    return base_dir + f'_{target_name}_{run}{suffix}' + write_tag


def _caption_pngs(in_dir, fits_file, target, label):
    """Caption and make PNG files"""
    imghdr = fits.open(os.path.join(in_dir, fits_file + FITS_EXT))[0].header
    center_freq = imghdr['CRVAL3'] / 1e6
    caption = f'{target.name} Continuum ({center_freq:.0f} MHz) ({label})'
    _make_pngs(in_dir, fits_file, caption)


def _update_metadata_imagedata(metadata, out_filebase, i):
    """Update the filenames and image data in the metadata dictionary"""
    metadata['FITSImageFilename'] = [out_filebase + FITS_EXT]
    metadata['PNGImageFileName'] = [out_filebase + PNG_EXT]
    metadata['PNGThumbNailFileName'] = [out_filebase + '_tnail' + PNG_EXT]

    image_keys = ["IntegrationTime", "RightAscension", "Declination",
                  "DecRa", "Targets",  "KatpointTargets"]
    for key in image_keys:
        metadata[key] = [metadata[key][i]]


def write_metadata(metadata, out_dir):
    """Write metadata file to out_dir"""
    try:
        metadata_file = os.path.join(out_dir, METADATA_JSON)
        log.info('Write metadata JSON: %s', metadata_file)
        with open(metadata_file, 'w') as meta:
            json.dump(metadata, meta)
    except Exception as e:
        log.warn("Creation of %s failed.\n%s", metadata_file, str(e))


def make_image_metadata(metadata, suffix, outdir, i, rname, desc, rmsnoise):
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
        'Description' value in new metadata file
    rmsnoise : float
         'RMSNoise' value in new metadata file
    """
    meta_suffix = copy.deepcopy(metadata)
    out_file = metadata['FITSImageFilename'][i]

    out_filebase = os.path.splitext(out_file)[0]
    out_filebase_suffix = out_filebase + suffix
    _update_metadata_imagedata(meta_suffix, out_filebase_suffix, i)

    meta_suffix['ProductType']['ReductionName'] = rname
    desc_prefix = meta_suffix['Description'].split(':')[0]
    meta_suffix['Description'] = desc_prefix + f': {desc}'

    meta_suffix['RMSNoise'] = str(rmsnoise)
    write_metadata(meta_suffix, outdir)


def make_pbeam_images(metadata, in_dir, write_tag):
    """Write primary beam corrected images.

    Make a single plane FITS image, a PNG and a thumbnail
    in a new directory per target. Write a new metadata file
    per target.

    Parameters
    ----------
    metadata : dict
        dictionary containing pipeline metadata
    in_dir : str
        path containing pipeline output files
    write_tag : str
        tag appended to directory name to indicate it is still being written to
    """
    filenames = metadata['FITSImageFilename']
    for i, in_file in enumerate(filenames):
        kat_target = katpoint.Target(metadata['KatpointTargets'][i])

        out_filebase = os.path.splitext(in_file)[0]
        out_filebase_pb = out_filebase + '_PB'
        log.info('Write primary beam corrected FITS output: %s',
                 out_filebase_pb + FITS_EXT)

        in_path = os.path.join(in_dir + write_tag, in_file)
        pb_dir = _productdir(metadata, in_dir, i, '_PB', write_tag)

        os.mkdir(pb_dir)
        pbc_path = os.path.join(pb_dir, out_filebase_pb + FITS_EXT)
        bp = pbc.beam_pattern(in_path)
        raw_image = pbc.read_fits(in_path)
        pbc_image = pbc.primary_beam_correction(bp, raw_image, px_cut=0.1)
        pbc.write_new_fits(pbc_image, in_path, outputFilename=pbc_path)

        log.info('Write primary beam corrected PNG output: %s',
                 out_filebase_pb + PNG_EXT)
        _caption_pngs(pb_dir, out_filebase_pb,
                      kat_target, 'PB Corrected')


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


def make_qa_report(metadata, base_dir, write_tag):
    """Write the QA report.

    Parameters
    ----------
    metadata : dict
        dictionary containing pipeline metadata
    base_dir : str
        append the target name and '_PB' to this to obtain the
        directory containing the primary beam corrected image.
    write_tag : str
        tag appended to directory name to indicate it is still being written to
    """
    # Change directory as QA code writes output directly to the running directory
    work_dir = os.getcwd()

    filenames = metadata['FITSImageFilename']
    for i, fits_file in enumerate(filenames):
        pb_dir = _productdir(metadata, base_dir, i, '_PB', write_tag)
        pb_filebase = os.path.splitext(fits_file)[0] + '_PB'

        log.info('Write QA report output')
        os.chdir(pb_dir)
        pb_fits = os.path.join(pb_dir, pb_filebase + FITS_EXT)
        command = '/home/kat/valid/Radio_continuum_validation -I {} --telescope MeerKAT -F'\
                  ' /home/kat/valid/filter_config_MeerKAT.txt -r'.format(pb_fits)
        sysarg = shlex.split(command)
        with log_qa(log):
            rcv.main(sysarg[0], sysarg[1:])
    os.chdir(work_dir)


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


def _add_missing_axes(fitsimage):
    """Aegean strips off the first two axes of fits image, restore them"""
    image = fits.open(fitsimage)
    hdr = image[0].header
    data = image[0].data

    new_hdu = fits.PrimaryHDU(data[np.newaxis, np.newaxis, :, :], hdr)
    new_hdu.writeto(fitsimage, overwrite=True)


def _calc_rms(rmsimage):
    rms_data = fits.open(rmsimage)[0].data
    rms_valid = rms_data > 0
    mean_rms = np.median(rms_data[rms_valid])
    return mean_rms


def organise_qa_output(metadata, base_dir, write_tag):
    """Organise QA output into separate directories.

    Create dedicated directories for each QA product to be ingested in the
    archive. This includes the rms and mean images created by the QA report code
    and the QA report. Move the data products to their final directories and
    create a metadata file for each of them. A directory contains a single QA product
    per target, the target and product type are indicated by the suffix of the
    directory name.

    Parameters
    ----------
    metadata : dict
        dictionary containing pipeline metadata
    in_dir : str
        base name of the created directories, the target name and product type
        is appended to this
    write_tag : str
        tag appended to directory name to indicate it is still being written to
    """
    filenames = metadata['FITSImageFilename']
    for i, fits_file in enumerate(filenames):
        kat_target = katpoint.Target(metadata['KatpointTargets'][i])

        # Move QA report and create metadata
        pb_filebase = os.path.splitext(fits_file)[0] + '_PB'
        qa_report = pb_filebase + '_continuum_validation_snr5.0_int'
        pb_dir = _productdir(metadata, base_dir, i, '_PB', write_tag)

        qa_dir = _productdir(metadata, base_dir, i, '_QA', write_tag)
        os.mkdir(qa_dir)
        os.rename(os.path.join(pb_dir, qa_report), qa_dir)
        make_report_metadata(metadata, qa_dir)

        # Move RMS image and create metadata
        rms_dir = _productdir(metadata, base_dir, i, '_RMS', write_tag)
        os.mkdir(rms_dir)
        rms_image = pb_filebase + '_aegean_rms'
        mean_pb_rms = _calc_rms(os.path.join(pb_dir, rms_image + FITS_EXT))

        make_image_metadata(metadata, '_PB', pb_dir, i,
                            'Continuum Image PB corrected',
                            'Continuum image PB corrected',
                            mean_pb_rms)

        os.rename(os.path.join(pb_dir, rms_image + FITS_EXT),
                  os.path.join(rms_dir, rms_image + FITS_EXT))
        _add_missing_axes(os.path.join(rms_dir, rms_image + FITS_EXT))
        _caption_pngs(rms_dir, rms_image, kat_target, 'RMS PB Corrected')
        make_image_metadata(metadata, '_PB_aegean_rms', rms_dir, i,
                            'Continuum PB Corrected RMS Image',
                            'Continuum PB Corrected RMS image',
                            mean_pb_rms)

        # Move MEAN image and create metadata
        bkg_dir = _productdir(metadata, base_dir, i, '_BKG', write_tag)
        os.mkdir(bkg_dir)
        bkg_image = pb_filebase + '_aegean_bkg'
        os.rename(os.path.join(pb_dir, bkg_image + FITS_EXT),
                  os.path.join(bkg_dir, bkg_image + FITS_EXT))
        _add_missing_axes(os.path.join(bkg_dir, bkg_image + FITS_EXT))
        _caption_pngs(bkg_dir, bkg_image, kat_target, 'MEAN PB Corrected')
        make_image_metadata(metadata, '_PB_aegean_bkg', bkg_dir, i,
                            'Continuum PB Corrected Mean Image',
                            'Continuum PB Corrected Mean image',
                            mean_pb_rms)

        # Remove .writing tag
        dir_list = [pb_dir, qa_dir, rms_dir, bkg_dir]
        for product_dir in dir_list:
            os.rename(product_dir, os.path.splitext(product_dir)[0])
