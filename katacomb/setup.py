from glob import glob
from os.path import join as pjoin

from setuptools import setup, find_packages

PKG = 'katacomb'
DESCRIPTION = "MeerKAT SDP Continuum Pipeline"

setup(name=PKG,
    description=DESCRIPTION,
    long_description=DESCRIPTION,
    url='https://github.com/ska-sa/katsdpcontim',
    classifiers=[
        "Development Status :: 3 - Alpha",
        "Intended Audience :: Developers",
        "License :: Other/Proprietary License",
        "Operating System :: OS Independent",
        "Programming Language :: Python",
        "Topic :: Software Development :: Libraries :: Python Modules",
        "Topic :: Scientific/Engineering :: Astronomy",
    ],
    author='MeerKAT SDP Team',
    author_email='sdpdev+katsdpcontim@ska.ac.za',
    python_requires=">=3.6",
    install_requires=['astropy',
                      'attrs',
                      'cerberus>=1.1',
                      'dask[array]',
                      'katdal',
                      'katpoint',
                      'katsdpimageutils',
                      'katsdpservices',
                      'katsdptelstate',
                      'nose',
                      'numba',
                      'numpy',
                      'pretty-py3',
                      'pyyaml',
                      'scipy'],
    scripts=glob(pjoin('scripts', '*.py')),
    packages=find_packages(),
    package_data={PKG: [pjoin('conf', '*.in')]},
    use_katversion=True
)
