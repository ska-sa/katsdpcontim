#!/usr/bin/env python3
"""Check the compiled Obit features and record source/toolchain provenance."""
import json
import os
from pathlib import Path
import re
import subprocess
import sys


def output(*command):
    return subprocess.check_output(command, universal_newlines=True).strip()


def main(root):
    root = Path(root).resolve()
    obit = root / 'ObitSystem/Obit'
    # Import the extension built for this exact interpreter, not a system copy.
    sys.path.insert(0, str(obit / 'python'))
    import Obit
    import _Obit

    if Path(_Obit.__file__).resolve().parent != obit / 'python':
        raise RuntimeError('Python loaded an Obit extension outside the build tree')
    commit = output('git', '-C', str(root), 'rev-parse', 'HEAD')
    source_version = Obit.Version()
    if not source_version.endswith('g' + commit[:12]):
        raise RuntimeError('Obit version does not identify the fetched commit: ' + source_version)
    features = sorted(set(re.findall(r'-D(HAVE_[A-Z0-9_]+)=1',
                                    (obit / 'src/Makefile').read_text())))
    required = {'HAVE_GPU', 'HAVE_CFITSIO', 'HAVE_FFTW3', 'HAVE_GSL', 'HAVE_XMLRPC'}
    missing = required.difference(features)
    if missing:
        raise RuntimeError('Required Obit features missing: ' + ', '.join(sorted(missing)))
    optional = {'HAVE_WVR', 'HAVE_PLPLOT', 'HAVE_PGPLOT'}.intersection(features)
    if optional:
        raise RuntimeError('Disabled Obit features were unexpectedly enabled: ' + ', '.join(sorted(optional)))
    cflags = os.environ['OBIT_CFLAGS']
    if '-mavx2' in cflags.split() and 'HAVE_AVX2' not in features:
        raise RuntimeError('AVX2 was requested but the Obit SIMD implementation is disabled')
    cuda_objects = output('/usr/local/cuda/bin/cuobjdump', '--list-elf', str(obit / 'lib/libObit.so'))
    architectures = sorted(set(re.findall(r'\.(sm_[0-9]+)\.cubin', cuda_objects)))
    if not architectures:
        raise RuntimeError('No native CUDA code found in libObit.so')
    info = {
        'obit_commit': commit,
        'obit_version': source_version,
        'obit_repository': output('git', '-C', str(root), 'remote', 'get-url', 'origin'),
        'c_compiler': output(os.environ['OBIT_CC'], '--version').splitlines()[0],
        'cflags': cflags,
        'nvcc_version': output('/usr/local/cuda/bin/nvcc', '--version'),
        'nvccflags': os.environ['OBIT_NVCCFLAGS'],
        'cuda_architectures': architectures,
        'cuda_samples_version': os.environ['CUDA_SAMPLES_VERSION'],
        'python_executable': sys.executable,
        'python_version': sys.version,
        'features': features,
    }
    destination = root / 'build-info.json'
    destination.write_text(json.dumps(info, indent=2, sort_keys=True) + '\n')
    print(destination.read_text(), end='')


if __name__ == '__main__':
    main(sys.argv[1])
