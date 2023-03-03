#!/usr/bin/env python
import argparse
import logging
import os

import katdal

import katacomb.configuration as kc
from katacomb import pipeline_factory, aips_ant_nr
from katacomb.util import (get_and_merge_args,
                           setup_aips_disks,
                           recursive_merge,
                           apply_user_mask,
                           katdal_options,
                           export_options,
                           imaging_options)


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
    group = parser.add_argument_group("Data selection options")
    group.add_argument("-t",
                       "--targets",
                       default=None,
                       type=str,
                       help="Comma separated list of target names to image. "
                            "Default: All targets")
    group.add_argument("-c",
                       "--channels",
                       default=None,
                       type=lambda s: map(int, s.split(",")),
                       help="Range of channels to image, must be of the form <start>,<end>. "
                            "Default: Image all (unmasked) channels.")
    group.add_argument("--pols",
                       default="HH,VV",
                       type=str,
                       help="Which polarisations to copy from the archive. "
                            "Default: %(default)s")
    group.add_argument("-m",
                       "--mask",
                       default=None,
                       type=str,
                       help="Pickle file containing a static mask of channels "
                            "to flag for all times. Must have the same number "
                            "of channels as the input dataset. "
                            "Default: No mask")
    katdal_options(parser)
    export_options(parser)
    imaging_options(parser)
    return parser


def main():
    parser = create_parser()
    args = parser.parse_args()
    configure_logging(args)
    log.info("Reading data with applycal=%s", args.applycal)
    katdata = katdal.open(args.katdata, applycal=args.applycal, **args.open_kwargs)

    # Apply the supplied mask to the flags
    if args.mask:
        apply_user_mask(katdata, args.mask)
    # Get frequencies and convert them to MHz
    freqs = katdata.freqs/1e6
    # Condition to check if the observation is narrow based on the bandwidth
    bandwidth = freqs[-1] - freqs[0]
    cond_50mhz = 0 < bandwidth < 100  # 50MHz
    cond_100mhz = 100 < bandwidth < 200  # 100MHz
    # Set up katdal selection based on arguments
    kat_select = {'pol': args.pols}
    # Get band
    band = katdata.spectral_windows[katdata.spw].band
    # Set number of nif to 2 for narrowband
    if band == 'L' and cond_50mhz or cond_100mhz:
        kat_select['nif'] = 2
    else:
        kat_select['nif'] = args.nif

    if args.targets:
        kat_select['targets'] = args.targets
    if args.channels:
        start_chan, end_chan = args.channels
        kat_select['channels'] = slice(start_chan, end_chan)

    # Command line katdal selection overrides command line options
    kat_select = recursive_merge(args.select, kat_select)

    # Determine default .yaml files
    uvblavg_parm_file = args.uvblavg_config
    if os.path.isdir(uvblavg_parm_file):
        if band == "L" and cond_50mhz or cond_100mhz:
            log.info("Using parameter files for narrow {}-band".format(band))
            uvblavg_parm_file = os.path.join(
                uvblavg_parm_file, f"uvblavg_narrow_{band}.yaml"
            )
        else:
            log.info("Using parameter files for wide {}-band".format(band))
            uvblavg_parm_file = os.path.join(uvblavg_parm_file, f"uvblavg_{band}.yaml")
    log.info("UVBlAvg parameter file for %s-band: %s", band, uvblavg_parm_file)
    mfimage_parm_file = args.mfimage_config
    if os.path.isdir(mfimage_parm_file):
        if band == "L" and cond_50mhz:
            log.info("Using parameter files for narrow {}-band".format(band))
            mfimage_parm_file = os.path.join(
                mfimage_parm_file, f"mfimage_narrow50mhz_{band}.yaml"
            )
        if band == "L" and cond_100mhz:
            log.info("Using parameter files for narrow {}-band".format(band))
            mfimage_parm_file = os.path.join(
                mfimage_parm_file, f"mfimage_narrow100mhz_{band}.yaml"
            )
        else:
            log.info("Using parameter files for wide {}-band".format(band))
            mfimage_parm_file = os.path.join(mfimage_parm_file, f"mfimage_{band}.yaml")
    log.info("MFImage parameter file for %s-band: %s", band, mfimage_parm_file)
    # Get defaults for uvblavg and mfimage and merge user supplied ones
    uvblavg_args = get_and_merge_args(uvblavg_parm_file, args.uvblavg)
    mfimage_args = get_and_merge_args(mfimage_parm_file, args.mfimage)

    # Grab the cal refant from the katdal dataset and default to
    # it if it is available and hasn't been set by the user.
    ts = katdata.source.telstate
    refant = ts.get('cal_refant')
    if refant is not None and 'refAnt' not in mfimage_args:
        mfimage_args['refAnt'] = aips_ant_nr(refant)

    # Try and always average down to 1024 channels if the user
    # hasn't specified something else
    num_chans = len(katdata.channels)
    factor = num_chans // 1024
    if 'avgFreq' not in uvblavg_args:
        if factor > 1:
            uvblavg_args['avgFreq'] = 1
            uvblavg_args['chAvg'] = factor

    # Get the default config.
    dc = kc.get_config()

    # capture_block_id is used to generate AIPS disk filenames
    capture_block_id = katdata.name[0:10]

    if args.reuse:
        # Set up AIPS disk from specified directory
        if os.path.exists(args.reuse):
            aipsdirs = [(None, args.reuse)]
            log.info('Re-using AIPS data area: %s', aipsdirs[0][1])
            reuse = True
        else:
            msg = "AIPS disk at '%s' does not exist." % (args.reuse)
            log.exception(msg)
            raise IOError(msg)
    else:
        # Set up aipsdisk configuration from args.workdir
        aipsdirs = [(None, os.path.join(args.workdir, capture_block_id + '_aipsdisk'))]
        log.info('Using AIPS data area: %s', aipsdirs[0][1])
        reuse = False

    # Set up output configuration from args.outputdir
    fitsdirs = dc['fitsdirs']

    # Append outputdir to fitsdirs
    fitsdirs += [(None, args.outputdir)]
    log.info('Using output data area: %s', args.outputdir)

    kc.set_config(aipsdirs=aipsdirs, fitsdirs=fitsdirs,
                  output_id='', cb_id=capture_block_id)

    setup_aips_disks()

    pipeline = pipeline_factory('offline', katdata,
                                katdal_select=kat_select,
                                uvblavg_params=uvblavg_args,
                                mfimage_params=mfimage_args,
                                nvispio=args.nvispio,
                                clobber=args.clobber,
                                prtlv=args.prtlv,
                                reuse=reuse)

    # Execute it
    pipeline.execute()


if __name__ == "__main__":
    main()
