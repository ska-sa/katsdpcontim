ARG KATSDPDOCKERBASE_REGISTRY=sdp-docker-registry.kat.ac.za:5000

FROM $KATSDPDOCKERBASE_REGISTRY/docker-base-gpu-build as build

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
    libgsl0-dev \
    liblapacke-dev \
    libmotif-dev \
    libncurses5-dev \
    libreadline-dev \
    libxmlrpc-c++8-dev \
    libxmlrpc-core-c3-dev \
    python-is-python3 \
    subversion \
    # Required by bnmin1
    swig \
    wget \
    zlib1g-dev \
    # Obit seems not to optimize well with the default gcc-9 in focal
    # so use gcc-8 instead.
    gcc-8 \
    g++-8

# Update, upgrade and install packages
RUN apt-get update && \
    apt-get install -y $PACKAGES

# Make gcc-8 the default gcc.
RUN update-alternatives --install /usr/bin/gcc gcc /usr/bin/gcc-8 100 --slave /usr/bin/g++ g++ /usr/bin/g++-8

ARG KATSDPDOCKERBASE_MIRROR=http://sdp-services.kat.ac.za/mirror

# Get CUDA samples- Obit needs some headers from there
RUN CUDA_RUN_FILE=cuda_11.4.1_470.57.02_linux.run && \
    mirror_wget --progress=dot:mega "http://developer.download.nvidia.com/compute/cuda/11.4.1/local_installers/$CUDA_RUN_FILE" && \
    sh ./$CUDA_RUN_FILE --samples --silent && \
    mv /root/NVIDIA_CUDA-11.4_Samples /usr/local/cuda/samples

ENV KATHOME=/home/kat

# Now downgrade to kat
USER kat

ENV OBIT_REPO https://github.com/bill-cotton/Obit/trunk/ObitSystem
ENV OBIT_BASE_PATH=/home/kat/Obit
ENV OBIT=/home/kat/Obit/ObitSystem/Obit

# Retrieve Obit r651
RUN mkdir -p $OBIT_BASE_PATH && \
    svn co -q -r 651 $OBIT_REPO ${OBIT_BASE_PATH}/ObitSystem

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

# Install required python packages
ENV PATH="$PATH_PYTHON3" VIRTUAL_ENV="$VIRTUAL_ENV_PYTHON3"
RUN install_pinned.py -r /tmp/requirements.txt

# Install validation package
ENV VALIDATION_REPO https://github.com/ska-sa/MeerKAT-continuum-validation.git
ENV VALIDATION_BASE_PATH=/home/kat/valid

# Retrieve validation package
RUN mkdir -p $VALIDATION_BASE_PATH && \
    git clone $VALIDATION_REPO ${VALIDATION_BASE_PATH}

# Install katacomb
COPY --chown=kat:kat . $KATHOME/src/katsdpcontim
WORKDIR $KATHOME/src/katsdpcontim/katacomb
# Workaround to get katversion working for katacomb:
# create a '___version___' file and put it in the katacomb install dir
RUN pip install katversion
RUN python -c 'import katversion; print(katversion.get_version())' > ___version___

RUN pip install --no-deps . && pip check

#######################################################################

FROM $KATSDPDOCKERBASE_REGISTRY/docker-base-gpu-runtime
LABEL maintainer="sdpdev+katsdpcontim@ska.ac.za"

# Switch to root for package install
USER root

ENV PACKAGES \
    libcfitsio8 \
    libcurl4 \
    libfftw3-3 \
    libglib2.0-0 \
    libgsl0-dev \
    libncurses5 \
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

# Install system packages
COPY --from=build --chown=kat:kat /home/kat/Obit /home/kat/Obit

# Install Python ve
COPY --from=build --chown=kat:kat /home/kat/ve3 /home/kat/ve3
ENV PATH="$PATH_PYTHON3" VIRTUAL_ENV="$VIRTUAL_ENV_PYTHON3"

# Install validation package
COPY --from=build --chown=kat:kat /home/kat/valid /home/kat/valid
ENV PYTHONPATH=/home/kat/valid:$PYTHONPATH

# Set up Obit environment
ENV OBIT_BASE_PATH=/home/kat/Obit
ENV OBIT="$OBIT_BASE_PATH"/ObitSystem/Obit \
    OBITINSTALL="$OBIT_BASE_PATH" \
    OBIT_EXEC="$OBIT" \
    OBITSD="$OBIT_BASE_PATH"/ObitSystem/ObitSD
ENV PATH="$OBIT_BASE_PATH"/ObitSystem/Obit/bin:"$PATH"
ENV LD_LIBRARY_PATH="$OBIT_BASE_PATH"/ObitSystem/Obit/lib:${LD_LIBRARY_PATH}
ENV PYTHONPATH=$OBIT_BASE_PATH/ObitSystem/ObitTalk/python:$OBIT_BASE_PATH/ObitSystem/Obit/python:$OBIT_BASE_PATH/ObitSystem/ObitSD/python:${PYTHONPATH}

# Set the work directory to /home/kat
WORKDIR /home/kat

# Configure Obit/AIPS disks
RUN cfg_aips_disks.py

# Execute test cases
# The test_continuum_pipeline classes are run separately
# since Obit cannot handle multiple pipeline runs (>24) 
# in the same python thread.
RUN pytest -s --pyargs katacomb.tests.test_utils \
                       katacomb.tests.test_qa \
                       katacomb.tests.test_aips_facades \
                       katacomb.tests.test_aips_path \
                       katacomb.tests.test_uv_export
RUN pytest -s --pyargs katacomb.tests.test_continuum_pipeline::TestOnlinePipeline
RUN pytest -s --pyargs katacomb.tests.test_continuum_pipeline::TestOfflinePipeline
RUN pytest -s --pyargs katacomb.tests.test_continuum_pipeline::TestUVExportPipeline
