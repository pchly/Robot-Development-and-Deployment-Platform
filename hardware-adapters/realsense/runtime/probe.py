"""RealSense容器的枚举与有限帧开流验收工具。"""

import argparse
import json
import time

import pyrealsense2 as rs


def device_info(device: object, key: object | None) -> str | None:
    if key is None:
        return None
    try:
        if hasattr(device, "supports") and not device.supports(key):
            return None
        return str(device.get_info(key))
    except RuntimeError:
        return None


def enumerate_devices() -> list[dict[str, str | None]]:
    devices: list[dict[str, str | None]] = []
    for device in rs.context().query_devices():
        devices.append(
            {
                "name": device_info(device, rs.camera_info.name),
                "sdk_serial": device_info(device, rs.camera_info.serial_number),
                "asic_serial": device_info(
                    device, getattr(rs.camera_info, "asic_serial_number", None)
                ),
                "firmware_update_id": device_info(
                    device, getattr(rs.camera_info, "firmware_update_id", None)
                ),
                "physical_port": device_info(
                    device, getattr(rs.camera_info, "physical_port", None)
                ),
                "firmware": device_info(device, rs.camera_info.firmware_version),
                "product_id": device_info(device, rs.camera_info.product_id),
                "product_line": device_info(device, rs.camera_info.product_line),
                "usb_type": device_info(device, rs.camera_info.usb_type_descriptor),
            }
        )
    return devices


def capture_frames(
    serial: str,
    frame_limit: int,
    width: int,
    height: int,
    fps: int,
    warmup_frames: int,
) -> dict[str, float]:
    pipeline = rs.pipeline()
    config = rs.config()
    config.enable_device(serial)
    config.enable_stream(rs.stream.color, width, height, rs.format.rgb8, fps)
    config.enable_stream(rs.stream.depth, width, height, rs.format.z16, fps)
    total_started_at = time.monotonic()
    received = 0
    started = False
    try:
        pipeline.start(config)
        started = True
        for _ in range(warmup_frames):
            pipeline.wait_for_frames(5000)
        sample_started_at = time.monotonic()
        while received < frame_limit:
            frames = pipeline.wait_for_frames(5000)
            if frames.get_color_frame() and frames.get_depth_frame():
                received += 1
    finally:
        if started:
            pipeline.stop()
    sample_elapsed = time.monotonic() - sample_started_at
    total_elapsed = time.monotonic() - total_started_at
    return {
        "frames": float(received),
        "warmup_frames": float(warmup_frames),
        "sample_elapsed_s": sample_elapsed,
        "total_elapsed_s": total_elapsed,
        "observed_fps": received / sample_elapsed if sample_elapsed else 0.0,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--frames", type=int, default=0)
    parser.add_argument("--serial", default="")
    parser.add_argument("--width", type=int, default=640)
    parser.add_argument("--height", type=int, default=480)
    parser.add_argument("--fps", type=int, default=30)
    parser.add_argument("--warmup-frames", type=int, default=30)
    arguments = parser.parse_args()

    devices = enumerate_devices()
    result: dict[str, object] = {"devices": devices}
    if arguments.frames > 0:
        if not devices:
            raise SystemExit("没有发现RealSense设备")
        serial = arguments.serial or str(devices[0]["sdk_serial"] or "")
        if not serial:
            raise SystemExit("设备没有可用序列号")
        result["capture"] = capture_frames(
            serial,
            arguments.frames,
            arguments.width,
            arguments.height,
            arguments.fps,
            arguments.warmup_frames,
        )
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
