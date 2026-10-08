ARG BASE_IMAGE=robot-platform/yolox-runtime:0.1.2-trt10.3
FROM ${BASE_IMAGE}

ARG DEBIAN_FRONTEND=noninteractive
ARG PYREALSENSE_WHEEL=pyrealsense2-2.58.4.10922-cp310-cp310-manylinux2014_aarch64.whl

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        libudev1 \
        libusb-1.0-0 \
    && rm -rf /var/lib/apt/lists/*

COPY vendor/${PYREALSENSE_WHEEL} /tmp/${PYREALSENSE_WHEEL}
RUN python3 -m pip install --no-cache-dir --no-deps "/tmp/${PYREALSENSE_WHEEL}" \
    && rm -f "/tmp/${PYREALSENSE_WHEEL}"

WORKDIR /opt/robot-runtime
COPY infer_image.py /opt/robot-runtime/infer_image.py
COPY latest_frame.py /opt/robot-runtime/latest_frame.py
COPY realsense_selector.py /opt/robot-runtime/realsense_selector.py
COPY realsense_yolox_runtime.py /opt/robot-runtime/realsense_yolox_runtime.py

ENV PYTHONUNBUFFERED=1

ENTRYPOINT ["python3", "/opt/robot-runtime/realsense_yolox_runtime.py"]
