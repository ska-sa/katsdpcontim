import logging
import os
import shlex
import sys

import contextlib
import katpoint
from astropy.io import fits

import Radio_continuum_validation as rcv
import katsdpimageutils.primary_beam_correction as pbc
from katacomb.aips_export import _make_pngs, FITS_EXT, PNG_EXT

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
        imghdr = fits.open(os.path.join(out_dir, out_filebase_pb + FITS_EXT))[0].header
        center_freq = imghdr['CRVAL3'] / 1e6
        caption = f'{target.name} Continuum ({center_freq:.0f} MHz) (PB Corrected)'
        _make_pngs(out_dir, out_filebase_pb, caption)


def make_qa_report(metadata, qa_dir):
    """Write the QA report.

    Parameters
    ---------
    metadata : dict
        dictionary containing pipeline metadata
    qa_dir : str
        path containing primary beam corrected images
    """
    # Change directory as QA code writes output directly to the running directory
    work_dir = os.getcwd()
    os.chdir(qa_dir)

    filenames = metadata['FITSImageFilename']
    for fits_file in filenames:
        pb_filebase = os.path.splitext(fits_file)[0] + '_PB'
        log.info('Write QA report output')

        pb_fits = os.path.join(qa_dir, pb_filebase + FITS_EXT)
        command = '/home/kat/valid/Radio_continuum_validation -I {} --telescope MeerKAT -F'\
                  ' /home/kat/valid/filter_config_MeerKAT.txt -r'.format(pb_fits)
        sysarg = shlex.split(command)
        with log_qa(log):
            rcv.main(sysarg[0], sysarg[1:])
    os.chdir(work_dir)


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
