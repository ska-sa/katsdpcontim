ARG KATSDPDOCKERBASE_REGISTRY=harbor.sdp.kat.ac.za/dpp

FROM $KATSDPDOCKERBASE_REGISTRY/docker-base-gpu-build as build

# Build only the core/tasks and Python bindings; WVR and plotting are disabled.
USER root
RUN apt-get update && apt-get install -y --no-install-recommends \
        build-essential ca-certificates curl git gfortran gcc-8 g++-8 pkg-config \
        libcfitsio-dev libcurl4-openssl-dev libfftw3-dev libglib2.0-dev \
        libgsl-dev libxmlrpc-core-c3-dev python3-dev python3-distutils zlib1g-dev && \
    rm -rf /var/lib/apt/lists/*

# Samples supply helper headers only. Keep them outside the CUDA toolkit tree.
ARG CUDA_SAMPLES_VERSION=11.4.1
RUN mkdir -p /opt/cuda-samples && \
    curl -fL "https://github.com/NVIDIA/cuda-samples/archive/refs/tags/v${CUDA_SAMPLES_VERSION}.tar.gz" \
        -o /tmp/cuda-samples.tar.gz && \
    tar -xzf /tmp/cuda-samples.tar.gz -C /opt/cuda-samples --strip-components=1 \
        "cuda-samples-${CUDA_SAMPLES_VERSION}/Common" && \
    rm /tmp/cuda-samples.tar.gz

ENV KATHOME=/home/kat

# Now downgrade to kat
USER kat

ARG OBIT_REPO=https://github.com/richarms/Obit.git
ENV OBIT_BASE_PATH=/home/kat/Obit
ENV OBIT=/home/kat/Obit/ObitSystem/Obit
# Pin the portable source branch by immutable commit when updating Obit.
ARG OBIT_REF=2f879587546f4049327b535fd5c2f79de96149df
# Production target: AVX2 CPUs and Pascal compute-capability 6.1 GPUs.
# Do not use -march=native or detect the build machine's GPU.
ARG OBIT_CC=gcc-8
ARG OBIT_CFLAGS="-g -O3 -mavx2"
ARG OBIT_NVCCFLAGS="-g -O3 -lineinfo -gencode=arch=compute_61,code=sm_61"
ARG OBIT_BUILD_JOBS=8

# Compile the extension for the interpreter used by katacomb at runtime.
ENV PATH="$PATH_PYTHON3" VIRTUAL_ENV="$VIRTUAL_ENV_PYTHON3"

WORKDIR $KATHOME
RUN mkdir $OBIT_BASE_PATH && \
    cd $OBIT_BASE_PATH && \
    git init && \
    git remote add origin $OBIT_REPO && \
    git sparse-checkout set --no-cone ObitSystem && \
    git fetch --depth=1 origin $OBIT_REF && \
    git checkout --detach FETCH_HEAD && \
    test "$(git rev-parse HEAD)" = "$OBIT_REF"

WORKDIR $OBIT_BASE_PATH

# Configure owns feature detection and linking; no source patches are applied.
RUN cd "$OBIT" && \
    CC="$OBIT_CC" CFLAGS="$OBIT_CFLAGS" \
    NVCCFLAGS="$OBIT_NVCCFLAGS" PYTHON="$VIRTUAL_ENV/bin/python" \
    ./configure --prefix=/usr --without-plplot --without-pgplot --without-wvr \
        --with-cuda=/usr/local/cuda --with-cuda-samples=/opt/cuda-samples/Common && \
    make -j"$OBIT_BUILD_JOBS" && \
    make check-python check-simd && \
    CUDA_TEST_BUILD_ONLY=1 CUDA_TEST_OUTPUT="$OBIT/test/testGPUContextCUDA" make check-gpu

COPY --chown=kat:kat scripts/record_obit_build.py /tmp/record_obit_build.py
RUN PYTHONPATH="$OBIT/python" python /tmp/record_obit_build.py "$OBIT_BASE_PATH"

# Add python package requirements
COPY --chown=kat:kat katacomb/requirements.txt /tmp/requirements.txt

# Install required python packages
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

RUN apt-get update && apt-get install -y --no-install-recommends \
        libcfitsio8 libcurl4 libfftw3-single3 libglib2.0-0 libgsl23 libgslcblas0 \
        libgfortran5 libstdc++6 libxmlrpc-core-c3 zlib1g && \
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
                       katacomb.tests.test_imager \
                       katacomb.tests.test_qa \
                       katacomb.tests.test_aips_facades \
                       katacomb.tests.test_aips_path \
                       katacomb.tests.test_uv_export
RUN pytest -s --pyargs katacomb.tests.test_continuum_pipeline::TestOnlinePipeline
RUN pytest -s --pyargs katacomb.tests.test_continuum_pipeline::TestOfflinePipeline
RUN pytest -s --pyargs katacomb.tests.test_continuum_pipeline::TestUVExportPipeline
