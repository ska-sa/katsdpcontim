#!/usr/bin/env python
"""
Basic version of the continuum imaging pipeline.

(1) Opens a katdal observation
(2) For each selected scan
    (a) Writes an AIPS UV scan file
    (b) Runs Baseline Dependent Averaging Obit task
        on scan UV file to produce blavg UV file.
    (c) Merges blavg UV file into global merge file.
(3) Runs MFImage Obit task on global merge file.
(4) Writes calibration solutions and clean components to telstate
"""

import argparse
import logging
import os
import os.path
from os.path import join as pjoin
import time

import katdal
from katsdpservices import setup_logging
from katsdptelstate import TelescopeState

import katacomb.configuration as kc
from katacomb import pipeline_factory, aips_ant_nr, make_pbeam_images, make_qa_report
from katacomb.util import (parse_python_assigns,
                           recursive_merge,
                           get_and_merge_args,
                           log_exception,
                           post_process_args,
                           setup_aips_disks)

log = logging.getLogger('katacomb')
# Tag to append to the output directory while the pipeline runs
WRITE_TAG = '.writing'
OUTDIR_SEPARATOR = '_'
START_TIME = '%d' % (int(time.time()*1000))
# Location of mfimage and uvblavg yaml configurations
CONFIG = '/obitconf'
# Minimum Walltime in sec. and extra time padding for MFImage
MINRUNTIME = 3. * 3600.
RUNTIME_PAD = 1.0


def create_parser():
    parser = argparse.ArgumentParser()

    parser.add_argument("katdata",
                        help="Katdal observation file")

    parser.add_argument('--access-key',
                        help='S3 access key to access the data')

    parser.add_argument('--secret-key',
                        help='S3 secret key to access the data')

    parser.add_argument('--token',
                        help='JWT to access the MeerKAT archive')

    parser.add_argument("-w", "--workdir",
                        default=pjoin(os.sep, 'scratch'), type=str,
                        help="Location of scratch space. An AIPS disk "
                             "will be created in this space. "
                             "Default: %(default)s")

    parser.add_argument("-o", "--outputdir",
                        default=pjoin(os.sep, 'var', os.sep, 'kat', os.sep, 'data'),
                        type=str,
                        help="Location to store output FITS, PNG files "
                             "and metadata dictionary. "
                             "Default: %(default)s")

    parser.add_argument("--nvispio", default=10240, type=int,
                        help="Number of visibilities per write when "
                             "copying data from archive. "
                             "Default: %(default)s")

    parser.add_argument("-cbid", "--capture-block-id",
                        default=None, type=str,
                        help="Capture Block ID. Unique identifier "
                             "for the observation on which the "
                             "continuum pipeline is run. "
                             "Default: Infer it from the katdal dataset.")

    parser.add_argument("-ts", "--telstate",
                        default='', type=str,
                        help="Address of the telstate server. "
                             "Default: Use a local telstate server.")

    parser.add_argument("-tsid", "--telstate-id",
                        default=None, type=str,
                        help="Namespace for output telescope "
                             "state keys (within the '-cbid' namespace). "
                             "Default: Value of --output-id")

    parser.add_argument("-oid", "--output-id",
                        default="continuum_image", type=str,
                        help="Label the product of the continuum pipeline. "
                             "Used to generate FITS and PNG filenames. "
                             "Default: %(default)s")

    parser.add_argument("-ks", "--select",
                        default="scans='track'; spw=0; corrprods='cross'",
                        type=log_exception(log)(parse_python_assigns),
                        help="katdal select statement "
                             "Should only contain python "
                             "assignment statements to python "
                             "literals, separated by semi-colons. "
                             "Default: %(default)s")

    TDF_URL = "https://github.com/bill-cotton/Obit/blob/master/ObitSystem/Obit/TDF"

    parser.add_argument("-ba", "--uvblavg",
                        default="",
                        type=log_exception(log)(parse_python_assigns),
                        help="UVBLAVG task parameter assignment statement. "
                             "Should only contain python "
                             "assignment statements to python "
                             "literals, separated by semi-colons. "
                             "See %s/UVBlAvg.TDF for valid parameters. " % TDF_URL)

    parser.add_argument("-mf", "--mfimage",
                        default="",
                        type=log_exception(log)(parse_python_assigns),
                        help="MFImage task parameter assignment statement. "
                             "Should only contain python "
                             "assignment statements to python "
                             "literals, separated by semi-colons. "
                             "See %s/MFImage.TDF for valid parameters. " % TDF_URL)

    parser.add_argument("--nif", default=8, type=int,
                        help="Number of AIPS 'IFs' to equally subdivide the band. "
                             "NOTE: Must divide the number of channels after any "
                             "katdal selection. Default: %(default)s")
    return parser


def _infer_defaults_from_katdal(katds):
    """
    Infer some default parameters for MFImage and UVBlAvg based
    upon what we know from the katdal object.
    """

    uvblavg_params = {}
    mfimage_params = {}

    # Try and always average down to ~1024 channels if necessary
    num_chans = len(katds.channels)
    factor = num_chans // 1024
    if factor > 1:
        uvblavg_params['avgFreq'] = 1
        uvblavg_params['chAvg'] = factor

    # Get the reference antenna used by cal
    # and use the same one for self-cal
    ts = katds.source.telstate
    refant = ts.get('cal_refant')
    if refant is not None:
        mfimage_params['refAnt'] = aips_ant_nr(refant)

    # Get the total observed time (t_obs) currently selected in the
    # kat_adapter and set the MFImage:maxRealtime parameter to
    # max(mintime, t_obs*(1. + extra)).
    t_obs = katds.shape[0] * katds.dump_period
    maxRealtime = max(MINRUNTIME, t_obs * (1. + RUNTIME_PAD))
    mfimage_params['maxRealtime'] = float(maxRealtime)

    return uvblavg_params, mfimage_params


def main():
    setup_logging()
    parser = create_parser()
    args = parser.parse_args()

    # Open the observation
    if (args.access_key is not None) != (args.secret_key is not None):
        parser.error('--access-key and --secret-key must be used together')
    if args.access_key is not None and args.token is not None:
        parser.error('--access-key/--secret-key cannot be used with --token')
    open_kwargs = {}
    if args.access_key is not None:
        open_kwargs['credentials'] = (args.access_key, args.secret_key)
    elif args.token is not None:
        open_kwargs['token'] = args.token
    katdata = katdal.open(args.katdata, applycal='l1', **open_kwargs)

    post_process_args(args, katdata)

    uvblavg_args, mfimage_args = _infer_defaults_from_katdal(katdata)

    # Get config defaults for uvblavg and mfimage and merge user supplied ones
    user_uvblavg_args = get_and_merge_args(pjoin(CONFIG, 'uvblavg_MKAT.yaml'), args.uvblavg)
    user_mfimage_args = get_and_merge_args(pjoin(CONFIG, 'mfimage_MKAT.yaml'), args.mfimage)

    # Merge katdal defaults with user supplied defaults
    recursive_merge(user_uvblavg_args, uvblavg_args)
    recursive_merge(user_mfimage_args, mfimage_args)

    # Get the default config.
    dc = kc.get_config()
    # Set up aipsdisk configuration from args.workdir
    if args.workdir is not None:
        aipsdirs = [(None, pjoin(args.workdir, args.capture_block_id + '_aipsdisk'))]
    else:
        aipsdirs = dc['aipsdirs']
    log.info('Using AIPS data area: %s', aipsdirs[0][1])

    # Set up output configuration from args.outputdir
    fitsdirs = dc['fitsdirs']

    outputname = args.capture_block_id + OUTDIR_SEPARATOR + args.telstate_id + \
        OUTDIR_SEPARATOR + START_TIME

    outputdir = pjoin(args.outputdir, outputname)
    # Set writing tag for duration of the pipeline
    work_outputdir = outputdir + WRITE_TAG
    # Append outputdir to fitsdirs
    # NOTE: Pipeline is set up to always place its output in the
    # highest numbered fits disk so we ensure that is the case
    # here.
    fitsdirs += [(None, work_outputdir)]
    log.info('Using output data area: %s', outputdir)

    kc.set_config(aipsdirs=aipsdirs, fitsdirs=fitsdirs)

    setup_aips_disks()

    # Add output_id and capture_block_id to configuration
    kc.set_config(cfg=kc.get_config(), output_id=args.output_id, cb_id=args.capture_block_id)

    # Set up telstate link then create
    # a view based the capture block ID and output ID
    telstate = TelescopeState(args.telstate)
    view = telstate.join(args.capture_block_id, args.telstate_id)
    ts_view = telstate.view(view)

    katdal_select = args.select
    katdal_select['nif'] = args.nif

    # Create Continuum Pipeline
    pipeline = pipeline_factory('online', katdata, ts_view,
                                katdal_select=katdal_select,
                                uvblavg_params=uvblavg_args,
                                mfimage_params=mfimage_args,
                                nvispio=args.nvispio)

    # Execute it
    metadata = pipeline.execute()

    # Create directory for QA output
    work_outputdir_qa = outputdir + '_QA' + WRITE_TAG
    os.mkdir(work_outputdir_qa)

    log.info('Using QA data area: %s', outputdir + '_QA')

    make_pbeam_images(metadata, work_outputdir, work_outputdir_qa)
    make_qa_report(metadata, work_outputdir_qa)

    # Remove the writing tag from the output directory
    os.rename(work_outputdir, outputdir)
    os.rename(work_outputdir_qa, outputdir + '_QA')


if __name__ == "__main__":
    main()
