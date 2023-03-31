#!/usr/bin/env python
import argparse
import logging
import os

import katdal

from katacomb import pipeline_factory
from katacomb.util import (get_and_merge_args,
                           setup_aips_disks,
                           recursive_merge,
                           katdal_options,
                           export_options,
                           selection_options,
                           imaging_options,
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
    parser = argparse.ArgumentParser(description="MeerKAT offline continuum pipeline.")
    parser.add_argument("katdata", help="Katdal observation reference.")
    parser.add_argument("-w",
                        "--workdir",
                        default=os.path.join(os.sep, "scratch"),
                        type=str,
                        help="Location of scratch space. An AIPS disk "
                             "will be created in this space. Default: %(default)s")
    parser.add_argument("-r",
                        "--reuse",
                        default=None,
                        type=str,
                        help="Location of AIPS disk from which to read UV "
                             "data. This will skip reading the UV data from "
                             "the archive and no aipsdisk will be created in "
                             "--workdir. "
                             "Default: Read the UV data from the archive.")
    parser.add_argument("--clobber",
                        default="scans, avgscans",
                        type=lambda s: set(v.strip() for v in s.split(",")),
                        help="Class of AIPS/Obit output files to clobber. "
                             "'scans' => Individual scans. "
                             "'avgscans' => Averaged individual scans. "
                             "'merge' => Observation file containing merged, "
                             "averaged scans. "
                             "'clean' => Output CLEAN files. "
                             "'mfimage' => Output MFImage files. "
                             "Default: %(default)s")
    parser.add_argument("--log-level",
                        type=str,
                        default="INFO",
                        help="Logging level. Default: %(default)s")
    katdal_options(parser)
    selection_options(parser)
    export_options(parser)
    imaging_options(parser)
    return parser


def main():
    parser = create_parser()
    args = parser.parse_args()
    configure_logging(args)
    log.info("Reading data with applycal=%s", args.applycal)
    katdata = katdal.open(args.katdata, applycal=args.applycal, **args.open_kwargs)
    post_process_args(args, katdata)

    # Get defaults and katdal selection
    (uvblavg_defaults,
     mfimage_defaults,
     kat_select) = setup_selection_and_parameters(katdata, args)

    # Merge parameters for Obit from parameter files with command line parameters
    uvblavg_parm_file = get_parameter_file(katdata, args.uvblavg_config)
    log.info("UVBlAvg parameter file: %s", os.path.basename(uvblavg_parm_file))
    uvblavg_args = get_and_merge_args('uvblavg', uvblavg_parm_file, args.uvblavg)
    mfimage_parm_file = get_parameter_file(katdata, args.mfimage_config)
    log.info("MFImage parameter file: %s", os.path.basename(mfimage_parm_file))
    mfimage_args = get_and_merge_args('mfimage', mfimage_parm_file, args.mfimage)
    # Merge default Obit parameters derived from katdal
    uvblavg_args = recursive_merge(uvblavg_args, uvblavg_defaults)
    mfimage_args = recursive_merge(mfimage_args, mfimage_defaults)

    aipsdir = None
    # Set up AIPS disk from specified reuse directory
    if args.reuse:
        aipsdir = args.reuse
        if not os.path.exists(aipsdir):
            msg = "AIPS disk at '%s' does not exist." % (args.reuse)
            log.exception(msg)
            raise IOError(msg)
    setup_configuration(args, aipsdisks=aipsdir)
    setup_aips_disks()

    pipeline = pipeline_factory('offline', katdata,
                                katdal_select=kat_select,
                                uvblavg_params=uvblavg_args,
                                mfimage_params=mfimage_args,
                                nvispio=args.nvispio,
                                clobber=args.clobber,
                                prtlv=args.prtlv,
                                reuse=bool(args.reuse))

    # Execute it
    pipeline.execute()


if __name__ == "__main__":
    main()
