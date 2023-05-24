#!/usr/bin/env python
import argparse
import logging
import os

import katdal

from katacomb import pipeline_factory, AIPSPath
from katacomb.util import (recursive_merge,
                           get_and_merge_args,
                           setup_aips_disks,
                           katdal_options,
                           export_options,
                           selection_options,
                           setup_selection_and_parameters,
                           setup_configuration,
                           post_process_args,
                           get_parameter_file)

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
                        default=None,
                        help="Path to AIPS disk to store the AIPS UV file. "
                             "Default: $PWD/CBID_aipsdisk")
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
    post_process_args(args, katdata)

    uvblavg_defaults, _, kat_select = setup_selection_and_parameters(katdata, args)

    # Merge parameters for Obit from parameter files with command line parameters
    uvblavg_parm_file = get_parameter_file(katdata, args.uvblavg_config)
    log.info("UVBlAvg parameter file: %s", os.path.basename(uvblavg_parm_file))
    uvblavg_args = get_and_merge_args('uvblavg', uvblavg_parm_file, args.uvblavg)
    uvblavg_args = recursive_merge(uvblavg_args, uvblavg_defaults)

    # No FITS disk needed for this script
    config = setup_configuration(args, aipsdisks=args.aipsdisk, fitsdisks=[])
    setup_aips_disks()
    # Default file name = capture_block_id
    aname = args.aname if args.aname else config['cb_id']
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
