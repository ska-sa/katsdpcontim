import ast
import contextlib
import functools
import logging
import os
import pickle
import re
import sys

from pretty import pretty
import yaml
import dask.array as da
import numpy as np

import katacomb.configuration as kc
from katacomb import (obit_config_from_aips,
                      parameter_dir,
                      fits_dir)

from katdal.flags import STATIC

import ObitTask
from OTObit import addParam
import OSystem

import builtins

# builtin function whitelist
_BUILTIN_WHITELIST = frozenset(['slice'])
_missing = _BUILTIN_WHITELIST.difference(dir(builtins))
if len(_missing) > 0:
    raise ValueError("'%s' are not valid builtin functions.'" % list(_missing))

log = logging.getLogger('katacomb')

# Mapping from Obit log levels to python logging methods.
# Obit log levels are from Obit/src/ObitErr.c
OBIT_TO_LOG = {
  "no msg ": log.debug,     # OBIT_None
  "info   ": log.info,      # OBIT_InfoErr
  "warn   ": log.warning,   # OBIT_InfoWarn
  "trace  ": log.error,     # OBIT_Traceback
  "MildErr": log.error,     # OBIT_MildError
  "Error  ": log.error,     # OBIT_Error
  "Serious": log.error,     # OBIT_StrongError
  "Fatal  ": log.critical   # OBIT_Fatal
}

OBIT_LOG_PREAMBLE_LEN = 23
TDF_URL = "https://github.com/bill-cotton/Obit/blob/master/ObitSystem/Obit/TDF"
# Default location of MFImage and UVBlavg yaml configurations
CONFIG = parameter_dir


@contextlib.contextmanager
def log_obit_err(logger, istask=True):
    """ Trap Obit log messages written to stdout and send them to logger"""

    def parse_obit_task_message(msg):
        # Generate log entry from Obit error string from task
        # All Obit task logs have the form '<taskname>: <level> <timestamp> <message>\n'

        # Ignore empty lines
        if not msg.strip():
            return
        # Get the Obit task name (all text before first ':')
        # Lines without ':', just write to log.debug.
        try:
            taskname, msg_remain = msg.split(':', 1)
        except ValueError:
            return log.debug(msg)
        msg_remain = msg_remain.lstrip()
        # Log level string (up to 7 characters)
        log_level = msg_remain[:7]
        # Cut the timestamp in the Obit string
        message = taskname + ':' + msg_remain[OBIT_LOG_PREAMBLE_LEN:].rstrip()
        # Trap case of unknown Obit log level
        try:
            OBIT_TO_LOG[log_level](message, extra={"obit_task": taskname})
        except KeyError:
            log.info(msg)

    def parse_message(msg):
        # For Obit log output that doesn't come from tasks
        # Strip carriage returns (logging doesnt like them).
        msg = msg.rstrip()
        if not msg:
            return
        log.info(msg)

    original = sys.stdout
    # Is this an Obit task? Otherwise likely something like uv.Header()
    if istask:
        logger.write = parse_obit_task_message
    else:
        logger.write = parse_message
    # The go() method inside ObitTask needs sys.stdout.isatty
    logger.isatty = sys.stdout.isatty
    sys.stdout = logger
    yield
    sys.stdout = original


def post_process_args(args, kat_ds):
    """
    Perform post-processing on command line arguments.

    1. Capture Block ID set to katdal experiment ID if not present or
    found in kat_ds.name.
    2. Telstate ID set to value of output-id if not present.
    3. Set workdir to the current working directory if not present.

    Parameters
    ----------
    args : object
        Arguments created by :meth:`argparse.ArgumentParser.parse_args()`
    kat_ds : :class:`katdal`
        Katdal object

    Returns
    -------
    object
        Modified arguments
    """

    # Set capture block ID to experiment ID if not set or name doesn't exist
    capture_block_id = getattr(args, 'capture_block_id', None)
    if capture_block_id is None:
        try:
            args.capture_block_id = kat_ds.name[0:10]
        except AttributeError:
            args.capture_block_id = kat_ds.experiment_id
            log.warning("No capture block ID was specified or "
                        "found in katdal. "
                        "Using experiment_id '%s' instead.",
                        kat_ds.experiment_id)
    telstate_id = getattr(args, 'telstate_id', None)
    args.output_id = getattr(args, 'output_id', '')
    if telstate_id is None:
        args.telstate_id = args.output_id
    args.workdir = getattr(args, 'workdir', os.path.curdir)
    return args


def recursive_merge(source, destination):
    """
    Recursively merge dictionary in source with dictionary in
    desination. Return the merged dictionaries.
    Stolen from:
    https://stackoverflow.com/questions/20656135/python-deep-merge-dictionary-data
    """
    for key, value in source.items():
        if isinstance(value, dict):
            # get node or create one
            node = destination.setdefault(key, {})
            recursive_merge(value, node)
        else:
            destination[key] = value

    return destination


def get_and_merge_args(collection, config_file, args):
    """
    Make a dictionary out of a '.yaml' config file
    and merge it with user supplied dictionary in args.

    Parameters
    ----------
    collection: str
        Collection name in YAML file.
    config_file: str
        Path to configuration YAML file.
    args: dict
        Dictionary of arguments to add to configuration.

    Returns
    -------
    dict
        Dictionary containing configuration parameters.
    """

    if not os.path.exists(config_file):
        log.warning("Specified configuration file %s not found. "
                    "Using Obit default parameters.", config_file)
        out_args = {}
    else:
        out_args = yaml.safe_load(open(config_file))[collection]
    recursive_merge(args, out_args)
    return out_args


def parse_python_assigns(assign_str):
    """
    Parses a string, containing assign statements
    into a dictionary.

    .. code-block:: python

        h5 = katdal.open('123456789.h5')
        kwargs = parse_python_assigns("spw=3; scans=[1,2];"
                                      "targets='bpcal,radec';"
                                      "channels=slice(0,2048)")
        h5.select(**kwargs)

    Parameters
    ----------
    assign_str: str
        Assignment string. Should only contain assignment statements
        assigning python literals or builtin function calls, to variable names.
        Multiple assignment statements should be separated by semi-colons.

    Returns
    -------
    dict
        Dictionary { name: value } containing
        assignment results.
    """

    if not assign_str:
        return {}

    def _eval_value(stmt_value):
        # If the statement value is a call to a builtin, try evaluate it
        if isinstance(stmt_value, ast.Call):
            func_name = stmt_value.func.id

            if func_name not in _BUILTIN_WHITELIST:
                raise ValueError("Function '%s' in '%s' is not builtin. "
                                 "Available builtins: '%s'"
                                 % (func_name, assign_str, list(_BUILTIN_WHITELIST)))

            # Recursively pass arguments through this same function
            if stmt_value.args is not None:
                args = tuple(_eval_value(a) for a in stmt_value.args)
            else:
                args = ()

            # Recursively pass keyword arguments through this same function
            if stmt_value.keywords is not None:
                kwargs = {kw.arg: _eval_value(kw.value) for kw
                          in stmt_value.keywords}
            else:
                kwargs = {}

            return getattr(builtins, func_name)(*args, **kwargs)
        # Try a literal eval
        else:
            return ast.literal_eval(stmt_value)

    # Variable dictionary
    variables = {}

    # Parse the assignment string
    stmts = ast.parse(assign_str, mode='single').body

    for i, stmt in enumerate(stmts):
        if not isinstance(stmt, ast.Assign):
            raise ValueError("Statement %d in '%s' is not a "
                             "variable assignment." % (i, assign_str))

        # Evaluate assignment lhs
        values = _eval_value(stmt.value)

        # "a = b = c" => targets 'a' and 'b' with 'c' as result
        for target in stmt.targets:
            # a = 2
            if isinstance(target, ast.Name):
                variables[target.id] = values

            # Tuple/List unpacking case
            # (a, b) = 2
            elif isinstance(target, (ast.Tuple, ast.List)):
                # Require all tuple/list elements to be variable names,
                # although anything else is probably a syntax error
                if not all(isinstance(e, ast.Name) for e in target.elts):
                    raise ValueError("Tuple unpacking in assignment %d "
                                     "in expression '%s' failed as not all "
                                     "tuple contents are variable names.")

                # Promote for zip and length checking
                if not isinstance(values, (tuple, list)):
                    elements = (values,)
                else:
                    elements = values

                if not len(target.elts) == len(elements):
                    raise ValueError("Unpacking '%s' into a tuple/list in "
                                     "assignment %d of expression '%s' failed. "
                                     "The number of tuple elements did not match "
                                     "the number of values."
                                     % (values, i, assign_str))

                # Unpack
                for variable, value in zip(target.elts, elements):
                    variables[variable.id] = value
            else:
                raise TypeError("'%s' types are not supported"
                                "as assignment targets." % type(target))

    return variables


def log_exception(logger):
    """ Decorator that wraps the passed log object and logs exceptions """

    def decorator(func):
        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            try:
                return func(*args, **kwargs)
            except Exception:
                logger.exception("Exception in '%s'", func.__name__)
                raise

        return wrapper

    return decorator


def task_factory(name, aips_cfg_file=None, **kwargs):
    """
    Creates Obit tasks, applying any configuration options
    supplied in an AIPS configuration file and keyword arguments.

    The following creates an `MFImage` task, loading configuration
    options from `mfimage.in` and setting the name, class and
    sequence number of the input AIPS file on disk 1.

    .. code-block:: python

        mfimage = task_factory("MFImage", "mfimage.in",
                            DataType="AIPS", inName="DEEP2",
                            inClass="raw", inSeq=1, inDisk=1)
        mfimage.go()

    Invalid configuration options will generate warnings,
    but will not be applied to the task.

    Parameters
    ----------
    name: string
        Obit task name. "MFImage" for example.
    aips_cfg_file (optional): string
        AIPS config file
    **kwargs (optional): :obj:
        Any task options that should be applied
        to the returned task object.

    Returns
    -------
    :class:`ObitTask.ObitTask`
        Obit task object
    """
    # Load any supplied file configuration first,
    # then apply kwargs on top of this
    if aips_cfg_file is not None:
        file_kwargs = obit_config_from_aips(aips_cfg_file)
        file_kwargs.update(kwargs)
        kwargs = file_kwargs

    # Override user number from global configuration
    kwargs['user'] = OSystem.PGetAIPSuser()

    # Obit ignores these options
    for k in ('nFITS', 'nAIPS', 'AIPSuser'):
        kwargs.pop(k, None)

    # Create the Obit Task
    task = ObitTask.ObitTask(name)

    # Apply configuration options to the task
    for k, v in kwargs.items():
        try:
            setattr(task, k, v)
        except AttributeError as e:
            attr_err = "ObitTask instance has no attribute '{}'".format(k)
            if attr_err in str(e):
                # Assume this is a "hidden" parameter and add it to
                # the task parameters via addParam
                addParam(task, k, paramVal=v)
            else:
                raise
    return task


def task_defaults(name):
    """
    Return a dict containing the default paramaters associated
    with the Obit task provided in `name`.
    """
    task = ObitTask.ObitTask(name)
    return task._default_dict


def fractional_bandwidth(uv_desc):
    """
    Returns the fractional bandwidth, given a uv descriptor dictionary

    Parameters
    ----------
    uv_desc : dict
        UV descriptor

    Returns
    -------
    float
        fractional bandwidth
    """
    try:
        ctypes = uv_desc['ctype']
    except KeyError:
        raise KeyError("The following UV descriptor is missing "
                       "a 'ctype': %s" % pretty(uv_desc))

    ctypes = [ct.strip() for ct in ctypes]

    try:
        freq_idx = ctypes.index('FREQ')
    except ValueError:
        raise ValueError("The following UV descriptor is missing "
                         "FREQ in it's 'ctype' field: %s" % pretty(uv_desc))

    freq_crval = uv_desc['crval'][freq_idx]
    freq_cdelt = uv_desc['cdelt'][freq_idx]
    freq_naxis = uv_desc['inaxes'][freq_idx]

    f1 = freq_crval
    f2 = freq_crval + freq_naxis*freq_cdelt

    return 2.0*(f2 - f1) / (f2 + f1)


def fmt_bytes(nbytes):
    """ Returns a human readable string, given the number of bytes """
    for x in ['B', 'KB', 'MB', 'GB']:
        if nbytes < 1024.0:
            return "%3.1f%s" % (nbytes, x)
        nbytes /= 1024.0

    return "%.1f%s" % (nbytes, 'TB')


def setup_aips_disks():
    """
    Ensure that each AIPS and FITS disk (directory) exists.
    Creates a SPACE file within the aips disks.
    """
    cfg = kc.get_config()
    for url, aipsdir in cfg['aipsdirs']:
        # Create directory if it doesn't exist
        if not os.path.exists(aipsdir):
            log.info("Creating AIPS Disk '%s'", aipsdir)
            os.makedirs(aipsdir)
        # Create SPACE file
        space = os.path.join(aipsdir, 'SPACE')

        with open(space, 'a'):
            os.utime(space, None)

    for url, fitsdir in cfg['fitsdirs']:
        # Create directory if it doesn't exist
        if not os.path.exists(fitsdir):
            log.info("Creating FITS Disk '%s'", fitsdir)
            os.makedirs(fitsdir)


def normalise_target_name(name, used=[], max_length=None):
    """
        Check that name[:max_length] is not in used and
        append a integer suffix if it is.
    """
    def generate_name(name, i, ml):
        # Create suffix string
        i_name = '' if i == 0 else '_' + str(i)
        # Return concatenated string if ml is not set
        if ml is None:
            ml = len(name) + len(i_name)
            t_name = name
        else:
            # Work out amount of name to drop
            length = len(name) + len(i_name) - ml
            t_name = name if length <= 0 else name[:-length]
        # If the length of i_name is greater than ml
        # just warn and revert to straight append
        if len(i_name) >= ml:
            log.warning('Too many repetitions of name %s.', name)
            t_name = name
        o_name = ''.join(filter(None, [t_name, i_name]))
        return '{:{ml}.{ml}}'.format(o_name, ml=ml)

    name = re.sub(r'[^-A-Za-z0-9_]', '_', name)
    i = 0
    test_name = generate_name(name, i, max_length)
    while test_name in used:
        i += 1
        test_name = generate_name(name, i, max_length)
    return test_name


def apply_user_mask(kat_ds, mask_file):
    """
    Apply a channel mask defined in mask_file to the flags of a katdal
    dataset.
    TODO(tmauch)
    Replace once JIRA SPR176 is implemented

    Parameters
    ----------
    kat_ds : :class:`katdal.Dataset`
        katdal Dataset object

    mask_file : str
        filename of pickle containing a boolean iterable
        of the channel mask to apply. The channel mask
        should be an iterable of bools with the same shape
        of the channel axis of the flags in `kat_ds`.
    """

    try:
        # Open the mask pickle
        mask = pickle.load(open(mask_file, 'rb'))
        # Apply the mask to 'static' flag bit
        mask = np.array(mask, dtype=np.uint8) * STATIC
        mask = mask[np.newaxis, :, np.newaxis]
        kat_ds._corrected.flags = da.bitwise_or(kat_ds._corrected.flags, mask)
        # Ensure mask is applied by resetting selection
        kat_ds.select()
        log.info("Applying channel mask from: '%s'", mask_file)
    except Exception:
        log.exception('Unable to apply supplied mask file from: %s', mask_file)
        raise


def katdal_options(parser):
    """Add options to :class:ArgumentParser for katdal."""
    group = parser.add_argument_group("katdal options")
    group.add_argument("-a",
                       "--applycal",
                       default="l1",
                       type=str,
                       help="Apply calibration solutions to visibilities "
                            "before imaging. The list of desired solutions "
                            "is comma separated and each takes the form "
                            "'stream'.'product' where 'stream' is either of "
                            "'l1' (cal) or 'l2' (self-cal) and product is "
                            "one of 'K','G','B' for l1 and 'GPHASE', 'GAMP_PHASE' "
                            "for l2. You can also select 'default' (Apply l1.K, l1.G, l1.B "
                            "and l2.GPHASE) or 'all' (Apply all available solutions). "
                            "Default: %(default)s")
    group.add_argument("-ks",
                       "--select",
                       default="scans='track'; corrprods='cross'",
                       type=log_exception(log)(parse_python_assigns),
                       help="katdal select statement "
                            "Should only contain python "
                            "assignment statements to python "
                            "literals, separated by semi-colons. "
                            "Default: %(default)s")
    group.add_argument("--open-kwargs",
                       default="",
                       type=log_exception(log)(parse_python_assigns),
                       help="kwargs to pass to katdal.open() "
                            "Should only contain python "
                            "assignment statements to python "
                            "literals, separated by semi-colons. "
                            "Default: None")


def export_options(parser):
    """Add options to :class:ArgumentParser for data export to AIPS UV format."""
    group = parser.add_argument_group("Data export options")
    group.add_argument("--nvispio",
                       default=1024,
                       type=int,
                       help="Number of visibilities per write when copying data "
                            "from archive. Default: %(default)s")
    group.add_argument("-ba",
                       "--uvblavg",
                       default="",
                       type=log_exception(log)(parse_python_assigns),
                       help="UVBlAvg task parameter assignment statement. "
                            "Should only contain python "
                            "assignment statements to python "
                            "literals, separated by semi-colons. "
                            "See " + TDF_URL + "/UVBlAvg.TDF for valid parameters. "
                            "Default: None")
    group.add_argument("--uvblavg-config",
                       default=CONFIG,
                       type=str,
                       help="Either a configuration yaml file for UVBlAvg "
                            "or a path to which an appropriate file can be found. "
                            "Default: Appropriate file from katacomb/conf/parameters")
    group.add_argument("--nif",
                       default=None,
                       type=int,
                       help="Number of AIPS 'IFs' to equally subdivide the band. "
                            "NOTE: Must divide the number of channels after any "
                            "katdal selection. Default: 8 for wideband, 4 for n107M, "
                            "2 for n54M")


def imaging_options(parser):
    """Add options to :class:ArgumentParser for katsdpcontim imaging scripts."""
    group = parser.add_argument_group("Imaging options")
    group.add_argument("-mf",
                       "--mfimage",
                       default="",
                       type=log_exception(log)(parse_python_assigns),
                       help="MFImage task parameter assignment statement. "
                            "Should only contain python "
                            "assignment statements to python "
                            "literals, separated by semi-colons. "
                            "See " + TDF_URL + "/MFImage.TDF for valid parameters. "
                            "Default: None")
    group.add_argument("--mfimage-config",
                       default=CONFIG,
                       type=str,
                       help="Either a configuration yaml file for MFImage "
                            "or a path to which an approriate file can be found. "
                            "Default: Appropriate file from katacomb/conf/parameters")
    group.add_argument("--prtlv",
                       default=2,
                       type=int,
                       help="Integer between 0 and 5 indicating the desired "
                            "verbosity of MFImage. 0=None 5=Maximum. "
                            "Default: %(default)s")
    group.add_argument("-o",
                       "--outputdir",
                       default=os.path.join(os.sep, "scratch"),
                       type=str,
                       help="Output directory. FITS image files named <cb_id>_<target_name>.fits "
                            "will be placed here for each target. Default: %(default)s")


def selection_options(parser):
    """Add options to :class:ArgumentParser for data selection and masking options."""
    group = parser.add_argument_group("Data selection options")
    group.add_argument("-t",
                       "--targets",
                       default=None,
                       type=str,
                       help="Comma separated list of target names to copy. "
                            "Default: All targets")
    group.add_argument("-c",
                       "--channels",
                       default=None,
                       type=lambda s: map(int, s.split(",")),
                       help="Range of channels to use, must be of the form <start>,<end>. "
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


def setup_selection_and_parameters(katdata, args):
    """Return 3 Dictionaries based on a katdal object and selection args:
        1. uvblavg defaults
        2. mfimage defaults
        3. katdal selection
    """
    # Apply the supplied mask to the flags
    if getattr(args, 'mask', None):
        apply_user_mask(katdata, args.mask)
    # Set up katdal selection based on arguments
    kat_select = {}
    if getattr(args, 'pol', None):
        kat_select['pol'] = args.pols
    if getattr(args, 'targets', None):
        kat_select['targets'] = args.targets
    if getattr(args, 'channels', None):
        start_chan, end_chan = args.channels
        kat_select['channels'] = slice(start_chan, end_chan)
    if getattr(args, 'nif', None):
        kat_select['nif'] = args.nif
    # Get defaults from katdal object
    uvblavg_defaults, mfimage_defaults, select_defaults = infer_defaults_from_katdal(katdata)
    # Command line katdal selection overrides command line options
    kat_select = recursive_merge(args.select, kat_select)
    # Anything at the command line overrides the katdal defaults
    kat_select = recursive_merge(kat_select, select_defaults)

    return uvblavg_defaults, mfimage_defaults, kat_select


def setup_configuration(args, aipsdisks=None, fitsdisks=None):
    """Setup configuration and AIPS and FITS disk locations.

       aipsdisks and fitsdisks are a string or list of strings of paths to
       disk locations. Setting either of them overrides the defaults derived
       from args.
    """
    if aipsdisks is None:
        # Set up aipsdisk configuration from args.workdir
        aipsdisks = [os.path.join(args.workdir, args.capture_block_id + '_aipsdisk')]
    # Ensure aipsdirs is a list of strings
    if isinstance(aipsdisks, str):
        aipsdisks = [aipsdisks]
    if len(aipsdisks) > 0:
        log.info('Using AIPS data areas: %s', ', '.join(aipsdisks))
    aipsdirs = [(None, aipsdisk) for aipsdisk in aipsdisks]
    # First FITS disk is always static metadata dir
    static_fitsdisk = [fits_dir]
    if fitsdisks is None:
        fitsdisks = [args.outputdir]
    if isinstance(fitsdisks, str):
        fitsdisks = [fitsdisks]
    fitsdisks = static_fitsdisk + fitsdisks
    log.info('Using static data area: %s', fitsdisks[0])
    if len(fitsdisks) > 1:
        # The scripts use the highest numbered FITS disk as their 'output' area
        log.info('Using output data area: %s', fitsdisks[-1])
    fitsdirs = [(None, fitsdisk) for fitsdisk in fitsdisks]

    # Add disks, output_id and capture_block_id to configuration
    kc.set_config(aipsdirs=aipsdirs, fitsdirs=fitsdirs,
                  output_id=args.output_id, cb_id=args.capture_block_id)
    return kc.get_config()


def infer_defaults_from_katdal(katds):
    """
    Infer some default Obit and katdal selection parameters based
    upon what we know from the katdal object.
    """
    from katacomb import aips_ant_nr

    uvblavg_params = {}
    mfimage_params = {}
    katdal_select = {}

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

    katdal_select['nif'] = 8
    if katds.spectral_windows[katds.spw].bandwidth < 200.e6:
        # Narrow
        katdal_select['nif'] = 2
    return uvblavg_params, mfimage_params, katdal_select


def _infer_default_parameter_file(katds, online):
    """Work out default parameter filenames based on band and bandwidth."""
    sw = katds.spectral_windows[katds.spw]
    mode = 'narrow' if sw.bandwidth < 200.e6 else 'wide'
    parm_file = f"{mode}_{sw.band}.yaml"
    if online:
        parm_file = "MKAT_" + parm_file
    return parm_file


def get_parameter_file(katds, parm_file, online=False):

    default_file = _infer_default_parameter_file(katds, online)
    # uvblavg parameters
    if os.path.isdir(parm_file):
        parm_file = os.path.join(parm_file, default_file)
    return parm_file
