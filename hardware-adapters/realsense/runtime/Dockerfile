ARG BASE_IMAGE=ubuntu:22.04
ARG LIBREALSENSE_VERSION=v2.58.4
ARG FORCE_RSUSB_BACKEND=OFF

FROM ${BASE_IMAGE} AS builder
ARG DEBIAN_FRONTEND=noninteractive
ARG LIBREALSENSE_VERSION
ARG FORCE_RSUSB_BACKEND

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        build-essential \
        ca-certificates \
        cmake \
        git \
        libssl-dev \
        libusb-1.0-0-dev \
        libudev-dev \
        pkg-config \
        python3-dev \
    && rm -rf /var/lib/apt/lists/*

RUN git clone --branch "${LIBREALSENSE_VERSION}" --depth 1 \
        https://github.com/realsenseai/librealsense.git /src/librealsense \
    && cmake -S /src/librealsense -B /src/librealsense/build \
        -DCMAKE_BUILD_TYPE=Release \
        -DCMAKE_INSTALL_PREFIX=/usr/local \
        -DBUILD_EXAMPLES=OFF \
        -DBUILD_GRAPHICAL_EXAMPLES=OFF \
        -DBUILD_PYTHON_BINDINGS=ON \
        -DFORCE_RSUSB_BACKEND="${FORCE_RSUSB_BACKEND}" \
        -DPYTHON_EXECUTABLE=/usr/bin/python3 \
    && cmake --build /src/librealsense/build --parallel 8 \
    && cmake --install /src/librealsense/build

FROM ${BASE_IMAGE} AS runtime
ARG DEBIAN_FRONTEND=noninteractive

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        libudev1 \
        libusb-1.0-0 \
        python3 \
    && rm -rf /var/lib/apt/lists/*

COPY --from=builder /usr/local /usr/local
COPY --from=builder /usr/lib/python3/dist-packages/pyrealsense2 /usr/lib/python3/dist-packages/pyrealsense2
COPY probe.py /opt/robot-runtime/probe.py

ENV LD_LIBRARY_PATH=/usr/local/lib:/usr/local/lib/aarch64-linux-gnu \
    PYTHONPATH=/usr/local/lib:/usr/local/lib/python3.10/site-packages \
    PYTHONUNBUFFERED=1

ENTRYPOINT ["python3", "/opt/robot-runtime/probe.py"]
