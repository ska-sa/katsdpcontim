_VALID_DISK_TYPES = ["AIPS", "FITS"]

def _check_disk_type(dtype, check=True):
    """
    Checks that `dtype` is either "AIPS" or FITS",
    raising a `ValueError` if this is not the case.

    Parameters
    ----------
    dtype: string
        AIPS disk type
    check: boolean
        False to avoid the check and

    Raises
    ------
    ValueError
        Raised if `dtype` is not "AIPS" or "FITS"
        or if check is `False`
    """
    if not check or not dtype in _VALID_DISK_TYPES:
        raise ValueError("Invalid disk type '%s'. "
                        "Should be one of '%s'" % (
                            dtype, _VALID_DISK_TYPES))

class ObitFile(object):
    """
    A class representing the naming properties of
    either an AIPS or FITS file.

    Note instances of this merely abstract and encapsulate
    the naming properties of AIPS and FITS files,
    making it easier to pass this data as arguments.
    They do not abstract file access.

    Also, while FITS files don't technically have classes
    or sequences, these are defaulted to "fits" and 1, respectively.
    """
    def __init__(self, name, disk, aclass=None, seq=None, dtype=None):
        """
        Constructs an :class:`ObitFile`.

        Parameters
        ----------
        name: string
            File name
        disk: integer
            Disk on which the file is located
        aclass (optional): string
            AIPS file class
        seq (optional): integer
            AIPS file sequence number
        dtype (optional): string
            Disk type. Should be "AIPS" or "FITS".
            Defaults to "AIPS" if not provided.

        """
        if dtype is None:
            dtype = "AIPS"

        if dtype == "AIPS":
            # Provide sensible defaults for missing class and sequence
            self._aclass = "default" if aclass is None else aclass
            self._seq = 1 if seq is None else seq
        elif dtype == "FITS":
            # FITS file don't have class or sequences,
            # just provide something sensible
            self._aclass = "fits"
            self._seq = 1
        else:
            _check_disk_type(dtype, False)

        self._dtype = dtype
        self._name = name
        self._disk = disk

    def copy(self, name=None, disk=None, aclass=None, seq=None, dtype=None):
        """
        Returns a copy of this object. Supplied parameters can
        override properties transferred to the new object.
        """
        return ObitFile(self._name if name is None else name,
                        self._disk if disk is None else disk,
                        self._aclass if aclass is None else aclass,
                        self._seq if seq is None else seq,
                        self._dtype if dtype is None else dtype)


    @property
    def name(self):
        """ File name """
        return self._name

    @name.setter
    def name(self, value):
        """ File name """
        self._name = value

    @property
    def disk(self):
        """ File disk """
        return self._disk

    @disk.setter
    def disk(self, value):
        """ File disk """
        self._disk = value

    @property
    def dtype(self):
        """ File disk type """
        return self._dtype

    @dtype.setter
    def dtype(self, value):
        """ File disk type """
        _check_disk_type(value)
        self._dtype = value

    @property
    def aclass(self):
        """ File class """
        return self._aclass

    @aclass.setter
    def aclass(self, value):
        """ File class """
        self._aclass = value

    @property
    def seq(self):
        """ File sequence number """
        return self._seq

    @seq.setter
    def seq(self, value):
        """ File sequence number """
        self._seq = value

    def __str__(self):
        """ String representation """
        if self._dtype == "AIPS":
            return "%s.%s.%s on AIPS %d" % (self._name, self._aclass,
                                            self._seq, self._disk)
        elif self._dtype == "FITS":
            return "%s on FITS %d" % (self._name, self._disk)
        else:
            _check_disk_type(self._dtype, False)

def task_input_kwargs(ofile):
    """
    Returns
    -------
    dict
        Keyword arguments suitable for applying
        to an ObitTask as an input file.
    """
    if ofile.dtype == "AIPS":
        return { "DataType" : ofile.dtype,
                 "inName" : ofile.name,
                 "inClass" : ofile.aclass,
                 "inSeq" : ofile.seq,
                 "inDisk": ofile.disk }
    elif ofile.dtype == "FITS":
        return { "DataType" : ofile.dtype,
                 "inFile" : ofile.name }
    else:
        _check_disk_type(ofile.dtype, False)

def task_output_kwargs(ofile, name=None, disk=None, aclass=None, seq=None, dtype=None):
    """
    Returns
    -------
    dict
        Keyword arguments suitable for applying
        to an ObitTask as an output file.
    """
    dtype = ofile.dtype if dtype is None else dtype

    if dtype == "AIPS":
        return { "outDType" : dtype,
                 "outName" : ofile.name if name is None else name,
                 "outClass" : ofile.aclass if aclass is None else aclass,
                 "outSeq" : ofile.seq if seq is None else seq,
                 "outDisk" : ofile.disk if disk is None else disk }
    elif dtype == "FITS":
        return { "outDType" : dtype,
                 "outFile" : ofile.name }
    else:
        _check_disk_type(dtype, False)

def task_output2_kwargs(ofile, name=None, disk=None, aclass=None, seq=None, dtype=None):
    """
    Returns
    -------
    dict
        Keyword arguments suitable for applying
        to an ObitTask as an output file.
    """
    dtype = ofile.dtype if dtype is None else dtype

    # NB. There doesn't seem to be an out2DType

    if dtype == "AIPS":
        return { "out2Name" : ofile.name if name is None else name,
                 "out2Class" : ofile.aclass if aclass is None else aclass,
                 "out2Seq" : ofile.seq if seq is None else seq,
                 "out2Disk" : ofile.disk if disk is None else disk }
    elif dtype == "FITS":
        return { "out2File" : ofile.name }
    else:
        _check_disk_type(dtype, False)