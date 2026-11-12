ARG KATSDPDOCKERBASE_REGISTRY=harbor.sdp.kat.ac.za/dpp

FROM $KATSDPDOCKERBASE_REGISTRY/docker-base-gpu-build:production-tag28102026 AS build

# Switch to root for package install
USER root

ENV PACKAGES \
    bison \
    build-essential \
    curl \
    flex \
    gfortran \
    libblas-dev \
    libboost-all-dev \
    libcfitsio-dev \
    # Without libcurl Obit pretends it can't find an external xmlrpc
    libcurl4-openssl-dev \
    libfftw3-dev \
    libglib2.0-dev \
    libgsl-dev \
    liblapacke-dev \
    libmotif-dev \
    libncurses-dev \
    libreadline-dev \
    libxmlrpc-c++8-dev \
    libxmlrpc-core-c3-dev \
    python-is-python3 \
    subversion \
    # Required by bnmin1
    swig \
    wget \
    zlib1g-dev \
    # gcc-10 used instead of gcc-8 (focal) for jammy compatibility
    gcc-10 \
    g++-10

# Update, upgrade and install packages
RUN apt-get update && \
    apt-get install -y $PACKAGES

# Make gcc-10 the default gcc
RUN update-alternatives --install /usr/bin/gcc gcc /usr/bin/gcc-10 100 \
    --slave /usr/bin/g++ g++ /usr/bin/g++-10

# Get CUDA samples - Obit needs some headers from there.
# As of CUDA 11.6 the samples are no longer available in the toolkit
# so retrieve them from NVIDIA's github repo instead. The headers are
# placed in /usr/local/cuda/samples/common/inc which is where the GPU
# Obit build modified by 'obit.patch' looks for them.
WORKDIR /root
RUN CUDA_SAMPLES_VER="11.4.1" && \
    CUDA_SAMPLES_URL="https://github.com/NVIDIA/cuda-samples/archive/refs/tags/v${CUDA_SAMPLES_VER}.tar.gz" && \
    wget --progress=dot:mega ${CUDA_SAMPLES_URL} && \
    tar -xvf v${CUDA_SAMPLES_VER}.tar.gz && \
    mkdir -p /usr/local/cuda/samples/common/inc && \
    mv cuda-samples-${CUDA_SAMPLES_VER}/Common/* /usr/local/cuda/samples/common/inc && \
    rm -rf v${CUDA_SAMPLES_VER}.tar.gz

ENV KATHOME=/home/kat

# Now downgrade to kat
USER kat

ENV OBIT_REPO=https://github.com/bill-cotton/Obit
ENV OBIT_BASE_PATH=/home/kat/Obit
ENV OBIT=/home/kat/Obit/ObitSystem/Obit


# Pin Obit to a specific commit to ensure reproducible builds.
# Newer commits break test_cc_export and test_gains_export tests.
# Old working commit: c4e72dad762d1b29031d1e890747d965d1cc087a (used in focaluvpip)
# TODO: Investigate if newer Obit commits fix the test failures and update accordingly.
WORKDIR $KATHOME
RUN git clone -n --filter=tree:0 $OBIT_REPO && \
    cd $OBIT_BASE_PATH && \
    git sparse-checkout set --no-cone ObitSystem && \
    git checkout c4e72dad762d1b29031d1e890747d965d1cc087a

WORKDIR $OBIT_BASE_PATH

# Add OBIT patch
COPY --chown=kat:kat obit.patch /tmp/obit.patch

# Apply OBIT patch
RUN patch -p1 -N -s < /tmp/obit.patch

# Compile Obit
RUN cd ObitSystem/Obit && \
    ./configure --prefix=/usr --without-plplot --without-wvr && \
    make clean && \
    make versionupdate && \
    make -j 8

# Add python package requirements
COPY --chown=kat:kat katacomb/requirements.txt /tmp/requirements.txt

# Activate the base virtualenv (ve3) inherited from the base image.
# uv is available via PATH_PYTHON3 which includes ~/.local/bin from base image.
#ENV PATH="$PATH_PYTHON3" VIRTUAL_ENV="$VIRTUAL_ENV_PYTHON3"
ENV PATH="/home/kat/.local/bin:$PATH_PYTHON3" VIRTUAL_ENV="$VIRTUAL_ENV_PYTHON3"

# Compile and install required python packages into ve3
RUN uv pip compile /tmp/requirements.txt \
    -o /tmp/install/requirements.lock && \
    uv pip install --no-deps -r /tmp/install/requirements.lock && \
    uv pip check

# Install validation package — pin to uvpipjammy branch which fixes
# pandas 2.0 incompatibility (Series[:, None] removed, breaking matplotlib)
ENV VALIDATION_REPO=https://github.com/ska-sa/MeerKAT-continuum-validation.git
ENV VALIDATION_BASE_PATH=/home/kat/valid

RUN mkdir -p $VALIDATION_BASE_PATH && \
    git clone --branch uvpipjammy $VALIDATION_REPO ${VALIDATION_BASE_PATH}

# Install katacomb
COPY --chown=kat:kat . $KATHOME/src/katsdpcontim
WORKDIR $KATHOME/src/katsdpcontim/katacomb

# Legacy Python compatibility layer for Obit + katacomb
# Must be pinned to avoid setuptools>=81 breaking pkg_resources
RUN uv pip install --python /home/kat/ve3/bin/python \
    setuptools==80.9.0 wheel katversion

# Workaround to get katversion working for katacomb:
# create a '___version___' file and put it in the katacomb install dir
RUN cd $KATHOME/src/katsdpcontim/katacomb && \
    uv pip install katversion && \
    python -c 'import katversion; print(katversion.get_version())' > ___version___ && \
    uv pip install --no-deps . && \
    uv pip check

#######################################################################

FROM $KATSDPDOCKERBASE_REGISTRY/docker-base-gpu-runtime:production-tag28102026
LABEL maintainer="sdpdev+katsdpcontim@ska.ac.za"

# Switch to root for package install
USER root

ENV PACKAGES \
    libcfitsio9 \
    libcurl4 \
    libfftw3-3 \
    libglib2.0-0 \
    libgsl27 \
    libncurses6 \
    libreadline8 \
    libxm4 \
    libxmlrpc-core-c3 \
    libxmlrpc-c++8v5

RUN apt-get update && \
    apt-get install -y --no-install-recommends $PACKAGES && \
    rm -rf /var/lib/apt/lists/*

# Set up areas for image/metadata output
RUN mkdir -p /var/kat/data
RUN chown -R kat:kat /var/kat
VOLUME ["/var/kat/data/"]

RUN mkdir /scratch
RUN chown kat:kat /scratch
VOLUME ["/scratch"]

# Now downgrade to kat
USER kat

# Install Python virtualenv from build stage
COPY --from=build --chown=kat:kat /home/kat/ve3 /home/kat/ve3

# Activate the virtualenv and make uv available
#ENV PATH="$PATH_PYTHON3" VIRTUAL_ENV="$VIRTUAL_ENV_PYTHON3"
ENV PATH="/home/kat/.local/bin:$PATH_PYTHON3" VIRTUAL_ENV="$VIRTUAL_ENV_PYTHON3"

# Install validation package from build stage
COPY --from=build --chown=kat:kat /home/kat/valid /home/kat/valid

# Install Obit from build stage
COPY --from=build --chown=kat:kat /home/kat/Obit /home/kat/Obit

COPY --from=build --chown=kat:kat /home/kat/.local /home/kat/.local

# Set up Obit environment.
# OBIT_BASE_PATH must be set before PYTHONPATH references it.
# The egg directory is added first so _Obit.so is found correctly —
# on jammy the Obit build places _Obit inside an egg subdirectory
# rather than directly in the python directory as in focal.
ENV OBIT_BASE_PATH=/home/kat/Obit
ENV OBIT="$OBIT_BASE_PATH/ObitSystem/Obit" \
    OBITINSTALL="$OBIT_BASE_PATH" \
    OBIT_EXEC="$OBIT_BASE_PATH/ObitSystem/Obit" \
    OBITSD="$OBIT_BASE_PATH/ObitSystem/ObitSD"
ENV PATH="$OBIT_BASE_PATH/ObitSystem/Obit/bin:$PATH"
ENV LD_LIBRARY_PATH="$OBIT_BASE_PATH/ObitSystem/Obit/lib"

# Set PYTHONPATH in one instruction so all components are defined.
# Egg directory must come first so _Obit.so is importable.
ENV PYTHONPATH="$OBIT_BASE_PATH/ObitSystem/Obit/python/Obit-1.0-py3.10-linux-x86_64.egg:\
$OBIT_BASE_PATH/ObitSystem/ObitTalk/python:\
$OBIT_BASE_PATH/ObitSystem/Obit/python:\
$OBIT_BASE_PATH/ObitSystem/ObitSD/python:\
/home/kat/valid"

ENV HOME=/home/kat
WORKDIR /home/kat

# Configure Obit/AIPS disks
RUN cfg_aips_disks.py

# Execute test cases
# The test_continuum_pipeline classes are run separately
# since Obit cannot handle multiple pipeline runs (>24)
# in the same python thread.
RUN uv pip install --python /home/kat/ve3/bin/python "pytest>=8"

RUN pytest -s --pyargs katacomb.tests.test_utils \
                       katacomb.tests.test_qa \
                       katacomb.tests.test_aips_facades \
                       katacomb.tests.test_aips_path \
                       katacomb.tests.test_uv_export \
                       --basetemp=/tmp/pytest
RUN pytest -s --pyargs katacomb.tests.test_continuum_pipeline::TestOnlinePipeline \
       --basetemp=/tmp/pytest
RUN pytest -s --pyargs katacomb.tests.test_continuum_pipeline::TestOfflinePipeline \
       --basetemp=/tmp/pytest
RUN pytest -s --pyargs katacomb.tests.test_continuum_pipeline::TestUVExportPipeline \
       --basetemp=/tmp/pytest
