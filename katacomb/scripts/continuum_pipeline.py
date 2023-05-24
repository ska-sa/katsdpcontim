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

from katacomb import pipeline_factory
from katacomb.util import (recursive_merge,
                           get_and_merge_args,
                           post_process_args,
                           setup_aips_disks,
                           katdal_options,
                           export_options,
                           imaging_options,
                           setup_configuration,
                           setup_selection_and_parameters,
                           get_parameter_file)
from katacomb.qa_report import (make_pbeam_images,
                                make_qa_report,
                                organise_qa_output)

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
    # Get defaults and katdal selection
    (uvblavg_defaults,
     mfimage_defaults,
     katdal_select) = setup_selection_and_parameters(katdata, args)

    # Merge Obit parameters Obit from parameter files with command line parameters
    uvblavg_parm_file = get_parameter_file(katdata, args.uvblavg_config, online=True)
    log.info("UVBlAvg parameter file: %s", os.path.basename(uvblavg_parm_file))
    uvblavg_args = get_and_merge_args('uvblavg', uvblavg_parm_file, args.uvblavg)
    mfimage_parm_file = get_parameter_file(katdata, args.mfimage_config, online=True)
    log.info("MFImage parameter file: %s", os.path.basename(mfimage_parm_file))
    mfimage_args = get_and_merge_args('mfimage', mfimage_parm_file, args.mfimage)
    # Merge default Obit parameters derived from katdal
    uvblavg_args = recursive_merge(uvblavg_args, uvblavg_defaults)
    mfimage_args = recursive_merge(mfimage_args, mfimage_defaults)

    # Get the total observed time (t_obs) currently in the
    # data and set the MFImage:maxRealtime parameter to
    # max(MINRUNTIME, t_obs*(1. + RUNTIME_PAD)).
    t_obs = katdata.shape[0] * katdata.dump_period
    maxRealtime = max(MINRUNTIME, t_obs * (1. + RUNTIME_PAD))
    mfimage_args['maxRealtime'] = float(maxRealtime)

    outputname = OUTDIR_SEPARATOR.join((args.capture_block_id, args.telstate_id, START_TIME))
    outputdir = pjoin(args.outputdir, outputname)
    # Set writing tag for duration of the pipeline
    work_outputdir = outputdir + WRITE_TAG

    setup_configuration(args, fitsdisks=work_outputdir)
    setup_aips_disks()

    # Set up telstate link then create
    # a view based the capture block ID and output ID
    telstate = TelescopeState(args.telstate)
    view = telstate.join(args.capture_block_id, args.telstate_id)
    ts_view = telstate.view(view)

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
