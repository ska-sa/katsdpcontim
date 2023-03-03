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
from katacomb import (pipeline_factory,
                      aips_ant_nr,
                      make_pbeam_images,
                      make_qa_report,
                      organise_qa_output)
from katacomb.util import (recursive_merge,
                           get_and_merge_args,
                           post_process_args,
                           setup_aips_disks,
                           katdal_options,
                           export_options,
                           imaging_options)

log = logging.getLogger('katacomb')
# Tag to append to the output directory while the pipeline runs
WRITE_TAG = '.writing'
OUTDIR_SEPARATOR = '_'
START_TIME = '%d' % (int(time.time()*1000))
# Minimum Walltime in sec. and extra time padding for MFImage
MINRUNTIME = 3. * 3600.
RUNTIME_PAD = 1.0


def create_parser():
    parser = argparse.ArgumentParser(description='MeerKAT online continuum pipeline.')
    parser.add_argument('katdata',
                        help='Katdal observation reference')
    parser.add_argument('--access-key',
                        help='S3 access key to access the data')
    parser.add_argument('--secret-key',
                        help='S3 secret key to access the data')
    parser.add_argument('--token',
                        help="JWT to access the MeerKAT archive")
    parser.add_argument('-w',
                        '--workdir',
                        default=pjoin(os.sep, 'scratch'),
                        type=str,
                        help='Location of scratch space. An AIPS disk '
                             'will be created in this space. '
                             'Default: %(default)s')
    parser.add_argument('-cbid',
                        '--capture-block-id',
                        default=None,
                        type=str,
                        help='Capture Block ID. Unique identifier '
                             'for the observation on which the '
                             'continuum pipeline is run. '
                             'Default: Infer it from the katdal dataset.')
    parser.add_argument("-ts",
                        "--telstate",
                        default="",
                        type=str,
                        help="Address of the telstate server. "
                             "Default: Use a local telstate server.")
    parser.add_argument("-tsid",
                        "--telstate-id",
                        default=None,
                        type=str,
                        help="Namespace for output telescope "
                             "state keys (within the '-cbid' namespace). "
                             "Default: Value of --output-id")
    parser.add_argument("-oid",
                        "--output-id",
                        default="continuum_image",
                        type=str,
                        help="Label the product of the continuum pipeline. "
                             "Used to generate FITS and PNG filenames. "
                             "Default: %(default)s")
    katdal_options(parser)
    export_options(parser)
    imaging_options(parser)
    parser.set_defaults(outputdir=pjoin(os.sep, "var", os.sep, "kat", os.sep, "data"),
                        select="scans='track'; spw=0; corrprods='cross'",
                        uvblavg="",
                        applycal="l1")

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
    band = katds.spectral_windows[katds.spw].band

    return uvblavg_params, mfimage_params, band


def main():
    setup_logging()
    parser = create_parser()
    args = parser.parse_args()
    # Open the observation
    if (args.access_key is not None) != (args.secret_key is not None):
        parser.error('--access-key and --secret-key must be used together')
    if args.access_key is not None and args.token is not None:
        parser.error('--access-key/--secret-key cannot be used with --token')
    open_kwargs = args.open_kwargs
    if args.access_key is not None:
        open_kwargs['credentials'] = (args.access_key, args.secret_key)
    elif args.token is not None:
        open_kwargs['token'] = args.token
    katdata = katdal.open(args.katdata, applycal=args.applycal, **open_kwargs)

    post_process_args(args, katdata)

    uvblavg_args, mfimage_args, band = _infer_defaults_from_katdal(katdata)
    # Get frequencies and convert them to MHz
    freqs = katdata.freqs/1e6
    # Condition to check if the observation is narrow based on the bandwidth
    bandwidth = freqs[-1] - freqs[0]
    cond_50mhz = 0 < bandwidth < 100  # 50MHz
    cond_100mhz = 100 < bandwidth < 200  # 100MHz
    # Get config defaults for uvblavg and mfimage and merge user supplied ones
    # Check if the observation is L-band and narrrow.
    uvblavg_parm_file = args.uvblavg_config
    if os.path.isdir(uvblavg_parm_file):
        if band == "L" and cond_50mhz or cond_100mhz:
            log.info("Using UVBlAvg parameter file for narrow {}-band".format(band))
            uvblavg_parm_file = os.path.join(
                uvblavg_parm_file, f"uvblavg_MKAT_narrow_{band}.yaml"
            )
        else:
            log.info("Using UVBlAvg parameter files for wide {}-band".format(band))
            uvblavg_parm_file = os.path.join(
                uvblavg_parm_file, f"uvblavg_MKAT_{band}.yaml"
            )
    log.info("UVBlAvg parameter file for %s-band: %s", band, uvblavg_parm_file)
    mfimage_parm_file = args.mfimage_config
    if os.path.isdir(mfimage_parm_file):
        if band == "L" and cond_50mhz:
            log.info("Using MFImage parameter file for narrow {}-band".format(band))
            mfimage_parm_file = os.path.join(
                mfimage_parm_file, f"mfimage_MKAT_narrow50mhz_{band}.yaml"
            )
        if band == "L" and cond_100mhz:
            log.info("Using MFImage parameter file for narrow {}-band".format(band))
            mfimage_parm_file = os.path.join(
                mfimage_parm_file, f"mfimage_MKAT_narrow100mhz_{band}.yaml"
            )
        else:
            log.info("Using MFImage parameter file for wide {}-band".format(band))
            mfimage_parm_file = os.path.join(
                mfimage_parm_file, f"mfimage_MKAT_{band}.yaml"
            )
    log.info("MFImage parameter file for %s-band: %s", band, mfimage_parm_file)

    user_uvblavg_args = get_and_merge_args(uvblavg_parm_file, args.uvblavg)
    user_mfimage_args = get_and_merge_args(mfimage_parm_file, args.mfimage)

    # Merge defaults with user supplied defaults
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
    # Setting number of nif to 2 for narrowband
    if cond_50mhz or cond_100mhz:
        katdal_select['nif'] = 2
    else:
        katdal_select['nif'] = args.nif

    # Create Continuum Pipeline
    pipeline = pipeline_factory('online', katdata, ts_view,
                                katdal_select=katdal_select,
                                uvblavg_params=uvblavg_args,
                                mfimage_params=mfimage_args,
                                nvispio=args.nvispio)

    # Execute it
    metadata = pipeline.execute()

    # Create QA products if images were created
    if metadata:
        make_pbeam_images(metadata, outputdir, WRITE_TAG)
        make_qa_report(metadata, outputdir, WRITE_TAG)
        organise_qa_output(metadata, outputdir, WRITE_TAG)

        # Remove the writing tag from the output directory
        os.rename(work_outputdir, outputdir)
    else:
        os.rmdir(work_outputdir)


if __name__ == "__main__":
    main()
