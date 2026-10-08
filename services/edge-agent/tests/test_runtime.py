"""Edge Agent 运行时测试。"""

from typing import Any

import pytest

from robot_edge_agent.runtime import build_registration, run_agent


class FakeClient:
    """记录注册和心跳调用的客户端。"""

    def __init__(self) -> None:
        self.registrations: list[dict[str, object]] = []
        self.heartbeats: list[tuple[str, int]] = []
        self.discovered_devices: list[list[dict[str, object]]] = []

    def register(self, payload: dict[str, object]) -> dict[str, Any]:
        self.registrations.append(payload)
        return {}

    def heartbeat(
        self,
        agent_id: str,
        sequence: int,
        metrics: dict[str, object] | None = None,
        devices: list[dict[str, object]] | None = None,
    ) -> dict[str, Any]:
        self.heartbeats.append((agent_id, sequence))
        self.discovered_devices.append(devices or [])
        return {}


def test_build_registration_deduplicates_capabilities() -> None:
    payload = build_registration(
        agent_id="edge-01",
        name="测试节点",
        capabilities=["simulation", "camera", "simulation"],
        installed_artifacts=[
            {
                "kind": "container_image",
                "locator": "robot-platform/demo:1.0",
            }
        ],
    )

    assert payload["agent_id"] == "edge-01"
    assert payload["capabilities"] == ["camera", "simulation"]
    assert payload["version"] == "0.21.0"
    assert payload["installed_artifacts"][0]["locator"] == "robot-platform/demo:1.0"
    assert "hostname" in payload["metadata"]
    assert "python_version" in payload["metadata"]


def test_run_once_registers_and_sends_first_heartbeat() -> None:
    client = FakeClient()
    registration = {"agent_id": "edge-01", "name": "测试节点"}

    run_agent(
        client,
        registration,
        once=True,
        discover_devices=lambda: [
            {
                "device_id": "usb-8086-0b5c-test",
                "transport": "usb",
                "name": "RealSense",
            }
        ],
    )

    assert client.registrations == [registration]
    assert client.heartbeats == [("edge-01", 0)]
    assert client.discovered_devices[0][0]["name"] == "RealSense"


def test_run_continuously_increments_sequence_and_waits() -> None:
    client = FakeClient()
    registration = {"agent_id": "edge-01", "name": "测试节点"}

    def stop_after_first_wait(seconds: float) -> None:
        assert seconds == 2.0
        raise RuntimeError("测试主动停止循环")

    with pytest.raises(RuntimeError, match="测试主动停止循环"):
        run_agent(
            client,
            registration,
            heartbeat_interval_s=2.0,
            sleep=stop_after_first_wait,
        )

    assert client.heartbeats == [("edge-01", 0)]


def test_heartbeat_interval_must_be_positive() -> None:
    client = FakeClient()

    try:
        run_agent(
            client,
            {"agent_id": "edge-01"},
            heartbeat_interval_s=0,
            once=True,
        )
    except ValueError as error:
        assert str(error) == "心跳间隔必须大于 0 秒"
    else:
        raise AssertionError("零心跳间隔应该被拒绝")


def test_agent_reconnects_with_exponential_backoff() -> None:
    class FlakyClient(FakeClient):
        def __init__(self) -> None:
            super().__init__()
            self.register_attempts = 0

        def register(self, payload: dict[str, object]) -> dict[str, Any]:
            self.register_attempts += 1
            if self.register_attempts < 3:
                raise OSError("平台暂时不可用")
            return super().register(payload)

    client = FlakyClient()
    sleep_delays: list[float] = []

    def record_sleep(seconds: float) -> None:
        sleep_delays.append(seconds)
        if seconds == 5.0:
            raise RuntimeError("测试主动停止循环")

    with pytest.raises(RuntimeError, match="测试主动停止循环"):
        run_agent(
            client,
            {"agent_id": "edge-01"},
            heartbeat_interval_s=5.0,
            reconnect_initial_s=1.0,
            reconnect_max_s=4.0,
            sleep=record_sleep,
        )

    assert client.register_attempts == 3
    assert client.heartbeats == [("edge-01", 0)]
    assert sleep_delays == [1.0, 2.0, 5.0]


def test_run_once_propagates_connection_error() -> None:
    class OfflineClient(FakeClient):
        def register(self, payload: dict[str, object]) -> dict[str, Any]:
            raise OSError("平台不可用")

    with pytest.raises(OSError, match="平台不可用"):
        run_agent(OfflineClient(), {"agent_id": "edge-01"}, once=True)


@pytest.mark.parametrize(
    ("initial", "maximum", "message"),
    [
        (0.0, 30.0, "初始重连间隔必须大于 0 秒"),
        (5.0, 2.0, "最大重连间隔不得小于初始重连间隔"),
    ],
)
def test_reconnect_intervals_are_validated(
    initial: float,
    maximum: float,
    message: str,
) -> None:
    with pytest.raises(ValueError, match=message):
        run_agent(
            FakeClient(),
            {"agent_id": "edge-01"},
            reconnect_initial_s=initial,
            reconnect_max_s=maximum,
            once=True,
        )
