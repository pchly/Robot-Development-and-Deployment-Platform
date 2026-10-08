"""PlatformClient 单元测试。"""

import hashlib
import io
import json
from pathlib import Path
from typing import Any

import pytest

from robot_edge_agent.client import PlatformClient


class FakeResponse:
    """模拟 urllib 响应上下文。"""

    def __enter__(self) -> "FakeResponse":
        return self

    def __exit__(self, *_args: object) -> None:
        return None

    def read(self, _size: int = -1) -> bytes:
        return b'{"status":"online"}'


class JsonResponse(FakeResponse):
    """返回指定JSON数据的模拟响应。"""

    def __init__(self, payload: object) -> None:
        self.payload = payload

    def read(self, _size: int = -1) -> bytes:
        return json.dumps(self.payload).encode("utf-8")


def test_register_posts_json_payload() -> None:
    captured: dict[str, Any] = {}

    def opener(request: Any, *, timeout: float) -> FakeResponse:
        captured["url"] = request.full_url
        captured["payload"] = json.loads(request.data)
        captured["timeout"] = timeout
        return FakeResponse()

    client = PlatformClient("http://localhost:8000/", timeout_s=2.0, opener=opener)

    result = client.register({"agent_id": "edge-01"})

    assert captured == {
        "url": "http://localhost:8000/api/v1/agents/register",
        "payload": {"agent_id": "edge-01"},
        "timeout": 2.0,
    }
    assert result == {"status": "online"}


def test_heartbeat_uses_agent_path() -> None:
    captured: dict[str, Any] = {}

    def opener(request: Any, *, timeout: float) -> FakeResponse:
        captured["url"] = request.full_url
        captured["payload"] = json.loads(request.data)
        return FakeResponse()

    client = PlatformClient("http://localhost:8000", opener=opener)

    client.heartbeat(
        "edge-01",
        4,
        {"uptime_s": 12.5},
        [{"device_id": "usb-8086-0b5c-test", "transport": "usb"}],
    )

    assert captured["url"].endswith("/api/v1/agents/edge-01/heartbeat")
    assert captured["payload"] == {
        "sequence": 4,
        "metrics": {"uptime_s": 12.5},
        "devices": [{"device_id": "usb-8086-0b5c-test", "transport": "usb"}],
    }


def test_heartbeat_can_omit_metrics() -> None:
    captured: dict[str, Any] = {}

    def opener(request: Any, *, timeout: float) -> FakeResponse:
        captured["payload"] = json.loads(request.data)
        return FakeResponse()

    client = PlatformClient("http://localhost:8000", opener=opener)

    client.heartbeat("edge-01", 2)

    assert captured["payload"] == {"sequence": 2}


def test_timeout_must_be_positive() -> None:
    try:
        PlatformClient("http://localhost:8000", timeout_s=0)
    except ValueError as error:
        assert str(error) == "请求超时必须大于 0 秒"
    else:
        raise AssertionError("零超时应该被拒绝")


def test_list_deployments_uses_agent_path() -> None:
    captured: dict[str, Any] = {}

    def opener(request: Any, *, timeout: float) -> JsonResponse:
        captured["url"] = request.full_url
        captured["method"] = request.method
        return JsonResponse([{"plan_id": "plan-01"}])

    client = PlatformClient("http://localhost:8000", opener=opener)

    result = client.list_deployments("edge-01")

    assert result == [{"plan_id": "plan-01"}]
    assert captured["url"].endswith("/api/v1/agents/edge-01/deployments")
    assert captured["method"] == "GET"


def test_report_deployment_status_posts_metrics() -> None:
    captured: dict[str, Any] = {}

    def opener(request: Any, *, timeout: float) -> FakeResponse:
        captured["url"] = request.full_url
        captured["payload"] = json.loads(request.data)
        return FakeResponse()

    client = PlatformClient("http://localhost:8000", opener=opener)

    client.report_deployment_status(
        "plan-01",
        "running",
        message="运行中",
        metrics={"configured_fps": 15.0},
    )

    assert captured["url"].endswith("/api/v1/deployments/plan-01/status")
    assert captured["payload"] == {
        "status": "running",
        "message": "运行中",
        "metrics": {"configured_fps": 15.0},
    }


def test_upload_deployment_preview_posts_jpeg_bytes() -> None:
    captured: dict[str, Any] = {}

    def opener(request: Any, *, timeout: float) -> JsonResponse:
        captured["url"] = request.full_url
        captured["method"] = request.method
        captured["content_type"] = request.headers["Content-type"]
        captured["data"] = request.data
        return JsonResponse({"size_bytes": len(request.data)})

    client = PlatformClient("http://localhost:8000", opener=opener)
    jpeg = b"\xff\xd8preview\xff\xd9"

    result = client.upload_deployment_preview("plan-01", jpeg)

    assert captured == {
        "url": "http://localhost:8000/api/v1/deployments/plan-01/preview",
        "method": "POST",
        "content_type": "image/jpeg",
        "data": jpeg,
    }
    assert result == {"size_bytes": len(jpeg)}


def test_upload_deployment_preview_posts_png_bytes() -> None:
    captured: dict[str, Any] = {}

    def opener(request: Any, *, timeout: float) -> JsonResponse:
        captured["content_type"] = request.headers["Content-type"]
        captured["data"] = request.data
        return JsonResponse({"size_bytes": len(request.data)})

    client = PlatformClient("http://localhost:8000", opener=opener)
    png = b"\x89PNG\r\n\x1a\npreviewIEND\xaeB`\x82"

    result = client.upload_deployment_preview("plan-01", png, content_type="image/png")

    assert captured == {"content_type": "image/png", "data": png}
    assert result == {"size_bytes": len(png)}


def test_artifact_installation_client_contract() -> None:
    captured: list[tuple[str, str, object]] = []

    def opener(request: Any, *, timeout: float) -> JsonResponse:
        captured.append(
            (
                request.method,
                request.full_url,
                json.loads(request.data) if request.data else None,
            )
        )
        if request.method == "GET":
            return JsonResponse([{"operation_id": "artifact-install-01"}])
        return JsonResponse({"status": "verifying"})

    client = PlatformClient("http://localhost:8000", opener=opener)
    plans = client.list_artifact_installations("edge-01")
    result = client.report_artifact_installation_status(
        "artifact-install-01", "verifying", message="校验中"
    )

    assert plans == [{"operation_id": "artifact-install-01"}]
    assert result == {"status": "verifying"}
    assert captured[0][1].endswith("/api/v1/agents/edge-01/artifact-installations")
    assert captured[1][1].endswith("/api/v1/artifact-installations/artifact-install-01/status")
    assert captured[1][2] == {"status": "verifying", "message": "校验中"}


def test_download_artifact_rejects_external_path_and_size_overflow() -> None:
    class BinaryResponse(FakeResponse):
        def read(self, size: int = -1) -> bytes:
            return b"12345"[:size]

    client = PlatformClient(
        "http://localhost:8000", opener=lambda *_args, **_kwargs: BinaryResponse()
    )

    assert client.download_artifact("/api/v1/artifacts/demo/content", max_bytes=5) == b"12345"
    assert (
        client.download_artifact(
            "/api/v1/artifacts/demo/content?variant_id=windows-amd64-python",
            max_bytes=5,
        )
        == b"12345"
    )
    with pytest.raises(OSError, match="不受信任"):
        client.download_artifact("https://evil.invalid/file", max_bytes=5)
    with pytest.raises(OSError, match="不受信任"):
        client.download_artifact(
            "/api/v1/artifacts/demo/content?variant_id=ok&redirect=https://evil.invalid",
            max_bytes=5,
        )
    with pytest.raises(OSError, match="超过"):
        client.download_artifact("/api/v1/artifacts/demo/content", max_bytes=4)


def test_download_artifact_to_file_streams_and_removes_invalid_content(
    tmp_path: Path,
) -> None:
    content = b"fixed container archive"

    class StreamResponse(FakeResponse):
        def __init__(self, payload: bytes) -> None:
            self._stream = io.BytesIO(payload)

        def read(self, size: int = -1) -> bytes:
            return self._stream.read(size)

    client = PlatformClient(
        "http://localhost:8000",
        opener=lambda *_args, **_kwargs: StreamResponse(content),
    )
    destination = tmp_path / "image.tar"
    digest = f"sha256:{hashlib.sha256(content).hexdigest()}"

    size = client.download_artifact_to_file(
        "/api/v1/artifacts/demo/content",
        destination,
        max_bytes=len(content),
        expected_digest=digest,
    )

    assert size == len(content)
    assert destination.read_bytes() == content

    invalid = tmp_path / "invalid.tar"
    with pytest.raises(OSError, match="SHA-256"):
        client.download_artifact_to_file(
            "/api/v1/artifacts/demo/content",
            invalid,
            max_bytes=len(content),
            expected_digest="sha256:" + "0" * 64,
        )
    assert not invalid.exists()

    with pytest.raises(OSError, match="不受信任"):
        client.download_artifact_to_file(
            "https://evil.invalid/image.tar",
            tmp_path / "external.tar",
            max_bytes=len(content),
            expected_digest=digest,
        )
    with pytest.raises(OSError, match="有效大小"):
        client.download_artifact_to_file(
            "/api/v1/artifacts/demo/content",
            tmp_path / "zero.tar",
            max_bytes=0,
            expected_digest=digest,
        )
