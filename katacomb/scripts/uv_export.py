#!/usr/bin/env python
import argparse
import logging
import os
from os.path import join as pjoin

import katdal

import katacomb.configuration as kc
from katacomb import pipeline_factory, AIPSPath
from katacomb.util import (recursive_merge,
                           get_and_merge_args,
                           setup_aips_disks,
                           katdal_options,
                           export_options,
                           selection_options,
                           setup_selection)

log = logging.getLogger('katacomb')


def configure_logging(args):
    log_handler = logging.StreamHandler()
    fmt = "[%(levelname)s] %(message)s"
    log_handler.setFormatter(logging.Formatter(fmt))
    log.addHandler(log_handler)
    log.setLevel(args.log_level.upper())


def create_parser():
    parser = argparse.ArgumentParser(description='Export MVF data to AIPS UV')
    parser.add_argument("katdata",
                        help="katdal observation reference",
                        type=str)
    parser.add_argument("--aname",
                        help="AIPS UV Name. Default: CBID",
                        type=str)
    parser.add_argument("--aclass",
                        default="merge",
                        help="AIPS UV Class. Default: %(default)s",
                        type=str)
    parser.add_argument("--aseq",
                        default=0,
                        help="AIPS UV seq. Default is next available on disk",
                        type=int)
    parser.add_argument("--aipsdisk",
                        help="Path to AIPS disk to store the AIPS UV file. "
                             "Default: CBID_aipsdisk")
    parser.add_argument("--average",
                        action='store_true',
                        help="Switch on BL dependent averaging and channel averaging")
    parser.add_argument("--log-level",
                        type=str,
                        default="INFO",
                        help="Logging level. Default: %(default)s")
    katdal_options(parser)
    selection_options(parser)
    export_options(parser)
    return parser


def main():
    parser = create_parser()
    args = parser.parse_args()
    configure_logging(args)
    log.info("Loading katdal dataset with applycal=%s", args.applycal)
    katdata = katdal.open(args.katdata, applycal=args.applycal, **args.open_kwargs)

    kat_select = setup_selection(katdata, args)
    # Command line katdal selection overrides command line options
    kat_select = recursive_merge(args.select, kat_select)

    # UVBlAvg parameters
    # TODO: Clean up this mess!
    uvblavg_args = {}
    if args.average:
        sw = katdata.spectral_windows[katdata.spw]
        uvblavg_parm_file = args.uvblavg_config
        if os.path.isdir(uvblavg_parm_file):
            if sw.band == "L" and sw.bandwidth < 200e6:
                log.info("Using parameter files for narrow {}-band".format(sw.band))
                uvblavg_parm_file = pjoin(uvblavg_parm_file,
                                          f"uvblavg_narrow_{sw.band}.yaml")
            else:
                log.info("Using parameter files for wide {}-band".format(sw.band))
                uvblavg_parm_file = pjoin(uvblavg_parm_file,
                                          f"uvblavg_{sw.band}.yaml")
        log.info("UVBlAvg parameter file for %s-band: %s", sw.band, uvblavg_parm_file)

        # Get defaults for uvblavg and mfimage and merge user supplied ones
        uvblavg_args = get_and_merge_args(uvblavg_parm_file, args.uvblavg)

    # capture_block_id is used to generate AIPS disk filenames
    capture_block_id = katdata.obs_params['capture_block_id']

    # Set up aipsdisk configuration
    aipsdisk = args.aipsdisk if args.aipsdisk else pjoin(capture_block_id + '_aipsdisk')
    aipsdirs = [(None, aipsdisk)]
    log.info('Using AIPS data area: %s', aipsdirs[0][1])

    # Set up configuration, AIPS disks and AIPS files for output
    # No FITS dir needed for uv_export.
    kc.set_config(aipsdirs=aipsdirs, fitsdirs=[], output_id='', cb_id=capture_block_id)
    setup_aips_disks()
    # Default file name = capture_block_id
    aname = args.aname if args.aname else capture_block_id
    # AIPS disk is always disk 1 in this script, default seq will be next available
    out_file = AIPSPath(aname, disk=1, aclass=args.aclass,
                        seq=args.aseq, atype='UV', dtype='AIPS')

    # Set up a pipeline and run it
    pipeline = pipeline_factory('continuum_export', katdata,
                                katdal_select=kat_select,
                                uvblavg_params=uvblavg_args,
                                merge_scans=not args.average,
                                nvispio=args.nvispio,
                                out_path=out_file)
    pipeline.execute()


if __name__ == "__main__":
    main()
