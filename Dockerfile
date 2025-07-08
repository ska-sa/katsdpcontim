ARG KATSDPDOCKERBASE_REGISTRY=harbor.sdp.kat.ac.za/dpp

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

# Get CUDA samples- Obit needs some headers from there.
# As of CUDA 11.6 the samples are no longer available in the toolkit
# so retrieve them from NVIDIA's github repo instead. The headers are
# placed in /usr/local/cuda/samples/inc which is where the GPU Obit
# build modified by 'obit.patch' looks for them.
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

ENV OBIT_REPO https://github.com/bill-cotton/Obit
ENV OBIT_BASE_PATH=/home/kat/Obit
ENV OBIT=/home/kat/Obit/ObitSystem/Obit

WORKDIR $KATHOME
RUN git clone -n --depth=1 --filter=tree:0 $OBIT_REPO && \
    cd $OBIT_BASE_PATH && \
    git sparse-checkout set --no-cone ObitSystem && \
    git checkout

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

# Set up Obit environment
ENV OBIT_BASE_PATH=/home/kat/Obit
ENV OBIT="$OBIT_BASE_PATH"/ObitSystem/Obit \
    OBITINSTALL="$OBIT_BASE_PATH" \
    OBIT_EXEC="$OBIT" \
    OBITSD="$OBIT_BASE_PATH"/ObitSystem/ObitSD
ENV PATH="$OBIT_BASE_PATH"/ObitSystem/Obit/bin:"$PATH"
ENV LD_LIBRARY_PATH="$OBIT_BASE_PATH"/ObitSystem/Obit/lib:${LD_LIBRARY_PATH}
ENV PYTHONPATH=$OBIT_BASE_PATH/ObitSystem/ObitTalk/python:$OBIT_BASE_PATH/ObitSystem/Obit/python:$OBIT_BASE_PATH/ObitSystem/ObitSD/python:${PYTHONPATH}

USER root
RUN cd /home/kat/Obit/ObitSystem/ObitTalk && \
    ./configure --prefix=/usr && \
    sed -i 's/$(TARGETS)/ /g' doc/Makefile && \
    make clean && \
    make && \
    make install 

COPY --chown=kat:kat configure_obitview /home/kat/Obit/ObitSystem/ObitView/configure

RUN cd /home/kat/Obit/ObitSystem/ObitView && \
    ./configure &&\
    make


RUN apt-get update && \
    apt-get install -y saods9

USER kat

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
