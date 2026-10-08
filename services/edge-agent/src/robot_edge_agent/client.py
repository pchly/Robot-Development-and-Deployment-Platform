"""Edge Agent 与 Platform API 的通信客户端。"""

import hashlib
import json
import re
from collections.abc import Callable
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlsplit
from urllib.request import Request, urlopen

UrlOpener = Callable[..., Any]


def _trusted_artifact_download_path(download_path: str) -> bool:
    """只接受同源Catalog内容端点及单个安全variant_id查询参数。"""

    parsed = urlsplit(download_path)
    if parsed.scheme or parsed.netloc or parsed.fragment:
        return False
    if not re.fullmatch(
        r"/api/v1/artifacts/[A-Za-z0-9._-]+/content",
        parsed.path,
    ):
        return False
    if not parsed.query:
        return True
    query = parse_qs(parsed.query, keep_blank_values=True, strict_parsing=True)
    return (
        set(query) == {"variant_id"}
        and len(query["variant_id"]) == 1
        and bool(re.fullmatch(r"[A-Za-z0-9._-]+", query["variant_id"][0]))
    )


class PlatformClient:
    """使用标准库 HTTP 客户端访问 Platform API。"""

    def __init__(
        self,
        server_url: str,
        *,
        timeout_s: float = 5.0,
        opener: UrlOpener = urlopen,
    ) -> None:
        if timeout_s <= 0:
            raise ValueError("请求超时必须大于 0 秒")
        self._server_url = server_url.rstrip("/")
        self._timeout_s = timeout_s
        self._opener = opener

    def register(self, payload: dict[str, object]) -> dict[str, Any]:
        """注册 Edge Agent 并返回平台记录。"""

        return self._post("/api/v1/agents/register", payload)

    def list_deployments(self, agent_id: str) -> list[dict[str, Any]]:
        """获取该节点当前应运行的全部部署计划。"""

        result = self._get(f"/api/v1/agents/{agent_id}/deployments")
        if not isinstance(result, list):
            raise OSError("Platform API返回了无效的部署计划列表")
        return result

    def list_artifact_installations(self, agent_id: str) -> list[dict[str, Any]]:
        """获取该节点当前待处理的受控制品安装计划。"""

        result = self._get(f"/api/v1/agents/{agent_id}/artifact-installations")
        if not isinstance(result, list):
            raise OSError("Platform API返回了无效的制品安装计划列表")
        return result

    def report_artifact_installation_status(
        self,
        operation_id: str,
        status: str,
        *,
        message: str = "",
    ) -> dict[str, Any]:
        """向平台回报制品安装事务状态。"""

        return self._post(
            f"/api/v1/artifact-installations/{operation_id}/status",
            {"status": status, "message": message},
        )

    def download_artifact(self, download_path: str, *, max_bytes: int) -> bytes:
        """从同一Platform API下载受大小限制的制品字节。"""

        if not _trusted_artifact_download_path(download_path):
            raise OSError("平台返回了不受信任的制品下载路径")
        request = Request(f"{self._server_url}{download_path}", method="GET")
        with self._opener(request, timeout=self._timeout_s) as response:
            content = response.read(max_bytes + 1)
        if len(content) > max_bytes:
            raise OSError("制品下载内容超过计划声明的大小限制")
        return content

    def download_artifact_to_file(
        self,
        download_path: str,
        destination: Path,
        *,
        max_bytes: int,
        expected_digest: str,
    ) -> int:
        """把大型制品流式写入临时文件，并在返回前校验大小与SHA-256。"""

        if not _trusted_artifact_download_path(download_path):
            raise OSError("平台返回了不受信任的制品下载路径")
        if max_bytes <= 0:
            raise OSError("制品计划没有声明有效大小")
        if not expected_digest.startswith("sha256:") or len(expected_digest) != 71:
            raise OSError("制品计划没有声明有效SHA-256")
        request = Request(f"{self._server_url}{download_path}", method="GET")
        total = 0
        digest = hashlib.sha256()
        try:
            with (
                self._opener(request, timeout=max(self._timeout_s, 60.0)) as response,
                destination.open("xb") as output,
            ):
                while True:
                    chunk = response.read(4 * 1024 * 1024)
                    if not chunk:
                        break
                    total += len(chunk)
                    if total > max_bytes:
                        raise OSError("制品下载内容超过计划声明的大小限制")
                    digest.update(chunk)
                    output.write(chunk)
            actual_digest = f"sha256:{digest.hexdigest()}"
            if actual_digest != expected_digest.casefold():
                raise OSError("制品下载内容SHA-256与计划不一致")
            return total
        except Exception:
            destination.unlink(missing_ok=True)
            raise

    def report_deployment_status(
        self,
        plan_id: str,
        status: str,
        *,
        message: str = "",
        metrics: dict[str, float] | None = None,
    ) -> dict[str, Any]:
        """向平台回报部署状态。"""

        return self._post(
            f"/api/v1/deployments/{plan_id}/status",
            {"status": status, "message": message, "metrics": metrics or {}},
        )

    def upload_deployment_preview(
        self,
        plan_id: str,
        image: bytes,
        *,
        content_type: str = "image/jpeg",
    ) -> dict[str, Any]:
        """上传一张受限大小的低频JPEG预览。"""

        if content_type not in {"image/jpeg", "image/png"}:
            raise ValueError("preview content type must be image/jpeg or image/png")

        request = Request(
            f"{self._server_url}/api/v1/deployments/{plan_id}/preview",
            data=image,
            headers={"Content-Type": content_type},
            method="POST",
        )
        with self._opener(request, timeout=self._timeout_s) as response:
            return json.loads(response.read().decode("utf-8"))

    def heartbeat(
        self,
        agent_id: str,
        sequence: int,
        metrics: dict[str, object] | None = None,
        devices: list[dict[str, object]] | None = None,
    ) -> dict[str, Any]:
        """发送一次节点心跳。"""

        payload: dict[str, object] = {"sequence": sequence}
        if metrics is not None:
            payload["metrics"] = metrics
        if devices is not None:
            payload["devices"] = devices
        return self._post(f"/api/v1/agents/{agent_id}/heartbeat", payload)

    def _post(self, path: str, payload: dict[str, object]) -> dict[str, Any]:
        request = Request(
            f"{self._server_url}{path}",
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with self._opener(request, timeout=self._timeout_s) as response:
            return json.loads(response.read().decode("utf-8"))

    def _get(self, path: str) -> Any:
        request = Request(f"{self._server_url}{path}", method="GET")
        with self._opener(request, timeout=self._timeout_s) as response:
            return json.loads(response.read().decode("utf-8"))
