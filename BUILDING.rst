Building the MFBeam production image
===================================

The new ``feat/mfbeam-production`` branch follows the existing offline
``mfbeam-imager`` integration. Its Obit source comes from ``richarms/Obit``'s
``feat/mfbeam-portable`` branch, pinned by full commit in ``Dockerfile``.
The source includes the Stokes, GPU ownership and SIMD numerical fixes.
Neither ``obit.patch`` nor the obsolete macOS patch is used or shipped.

The portable Obit configuration honours compiler flags and detects SIMD from
that compiler target. It does not inspect the build host's CPU/GPU, force AVX2,
or choose a GPU architecture. This container deliberately selects those
optimisations for the production fleet represented by imgr20:

* CPU: Intel Xeon E5-2643 v4 with AVX2; ``-g -O3 -mavx2``.
* GPU: GTX 1080 Ti, CUDA compute capability 6.1; native ``sm_61`` code.
* Container toolchain: Ubuntu 20.04, CUDA 11.4, Python 3.8 in ``/home/kat/ve3``.
  GCC/G++ 8 retain the existing compiler policy, without changing the system
  compiler alternatives.

Native CUDA code avoids depending on PTX JIT support from a newer driver.
These defaults target this fleet; select other architectures explicitly when
building an image for different GPUs. An AVX2 image requires AVX2 at runtime.

Build arguments
---------------

Run from this checkout::

    docker build -t katsdpcontim:mfbeam-production .

The base-image registry remains controlled by ``KATSDPDOCKERBASE_REGISTRY``.
The source reference must be a full immutable commit, available in
``OBIT_REPO``. Publish the Obit branch before building from a newly pinned
commit. Do not point a production build at a moving branch name.

The relevant arguments are:

========================== ============================================================
Argument                   Default / purpose
========================== ============================================================
``OBIT_REPO``              ``https://github.com/richarms/Obit.git``
``OBIT_REF``               Full commit recorded in ``Dockerfile``
``OBIT_CC``                ``gcc-8``
``OBIT_CFLAGS``            ``-g -O3 -mavx2``
``OBIT_NVCCFLAGS``         ``-g -O3 -lineinfo -gencode=arch=compute_61,code=sm_61``
``CUDA_SAMPLES_VERSION``   ``11.4.1``; only ``Common`` helper headers are extracted
``OBIT_BUILD_JOBS``        ``8``
========================== ============================================================

For example, select a baseline CPU target with
``--build-arg OBIT_CFLAGS='-g -O3'``. For a different GPU fleet, pass the
appropriate ``OBIT_NVCCFLAGS`` (multiple ``-gencode`` entries can be used).
The chosen CUDA toolkit must support every requested GPU architecture.

Build and runtime base images still follow the existing Jenkins dependency
policy. For release reproducibility, record both resolved image digests along
with the source commit and build arguments.

Build checks and provenance
---------------------------

The Docker build uses the same virtual-environment interpreter for the Obit
extension and katacomb. Configure disables WVR, PLplot and PGPLOT, and builds
shared libraries, tasks and bindings using Obit's own build system. Samples
headers are stored in ``/opt/cuda-samples/Common`` rather than modifying the
CUDA installation.

Before installing katacomb it runs:

* Python array arithmetic, FFT and FITS round-trip checks.
* Scalar/SIMD sine, cosine and exponential checks, including vector tails.
* Simulated GPU dispatch/ownership tests and CUDA lifecycle compile/link checks.

The build verifies the scientific/CUDA features, checks that Python loads the
extension from this build, and records the source commit, Obit version,
compiler versions, flags, SIMD features and native CUDA architectures in
``/home/kat/Obit/build-info.json``. Inspect the result with::

    docker run --rm --entrypoint cat katsdpcontim:mfbeam-production \
        /home/kat/Obit/build-info.json

The existing runtime-stage katacomb/pipeline regression tests are retained.
Actual GPU execution is a separate host check because Docker build workers
need not expose GPUs::

    docker run --rm --gpus all --entrypoint /bin/sh \
        katsdpcontim:mfbeam-production -c \
        'cd "$OBIT" && ./test/testGPUContextCUDA'

This runs the prebuilt lifecycle-test executable against the host driver;
it does not require the compiler in the runtime image. Follow it
with an actual MFBeam imaging run using the normal data/beam mounts and
``GPU_no=[0,1,2,3]``. Compare images and Stokes headers with the validated
reference. The lifecycle test alone does not validate image quality.

The existing backend remains opt-in: MFImage is still the default, and
MFBeam's default device list remains device 0. Build optimisation targets do
not determine which devices an imaging task uses.
