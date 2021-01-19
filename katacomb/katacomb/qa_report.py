import logging
import os
import shlex
import sys

import contextlib

import Radio_continuum_validation as rcv
import katsdpimageutils.primary_beam_correction as pbc
from katacomb.aips_export import _make_pngs, FITS_EXT, PNG_EXT

log = logging.getLogger('katacomb')


@contextlib.contextmanager
def redirect_argv(num):
    """Overwrite sys.argv parameters."""
    sys._argv = sys.argv[:]
    sys.argv = num
    yield
    sys.argv = sys._argv


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

    Make a single plane fits image, a png and a thumbnail
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
    targets = metadata['Targets']
    for target, out_file in zip(targets, filenames):
        out_filebase = os.path.splitext(out_file)[0]
        log.info('Write primary beam corrected FITS output: %s',
                 out_filebase + '_PB' + FITS_EXT)

        in_path = os.path.join(in_dir, out_file)
        pbc_path = os.path.join(out_dir, out_filebase + '_PB' + FITS_EXT)
        bp, raw_image = pbc.beam_pattern(in_path)
        pbc_image = pbc.primary_beam_correction(bp, raw_image, px_cut=0.1)
        pbc.write_new_fits(pbc_image, in_path, outputFilename=pbc_path)
        log.info('Write primary beam corrected PNG output: %s',
                 out_filebase + '_PB' + PNG_EXT)
        caption = f'{target}'
        _make_pngs(out_dir, out_filebase + '_PB', caption)


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
        command = '/home/kat/valid/Radio_cont_main -I {} --telescope MeerKAT -F'\
                  ' /home/kat/valid/filter_config_MeerKAT.txt -r'.format(pb_fits)
        sysarg = shlex.split(command)
        with redirect_argv(sysarg):
            with log_qa(log):
                rcv.main()
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
