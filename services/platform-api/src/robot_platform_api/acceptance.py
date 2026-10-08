"""根据终态运行证据生成可追溯的工程验收报告。"""

import math
from datetime import datetime

from robot_platform_api.models import (
    AcceptanceCheckStatus,
    AcceptanceVerdict,
    DeploymentAcceptanceCheck,
    DeploymentAcceptanceReport,
    DeploymentRecord,
    DeploymentStatus,
)

_EVIDENCE_METRICS = {
    "configured_fps",
    "control_loop_actual_fps",
    "control_tick_mean_ms",
    "control_tick_p95_ms",
    "control_deadline_misses",
    "runtime_ticks",
    "inference_mean_ms",
    "motion_commands_emitted",
    "motion_commands_accepted",
    "actuator_faults",
    "actuator_safe_stop",
    "mujoco_target_reached",
    "preview_actual_fps",
    "preview_age_latest_ms",
    "preview_frames_dropped",
}


def _metric(metrics: dict[str, float], name: str) -> float | None:
    value = metrics.get(name)
    if value is None or not math.isfinite(value):
        return None
    return float(value)


def _check(
    check_id: str,
    label: str,
    status: AcceptanceCheckStatus,
    actual: float | str | None,
    expected: str,
    *,
    unit: str | None = None,
    detail: str = "",
) -> DeploymentAcceptanceCheck:
    return DeploymentAcceptanceCheck(
        check_id=check_id,
        label=label,
        status=status,
        actual=actual,
        expected=expected,
        unit=unit,
        detail=detail,
    )


def _has_actuator(record: DeploymentRecord) -> bool:
    return any(
        component.kind == "hardware"
        and any(token in component.component_ref for token in ("arm", "actuator", "base"))
        for component in record.plan.components
    ) or any(name.startswith("actuator_") for name in record.metrics)


def build_acceptance_report(
    record: DeploymentRecord,
    *,
    generated_at: datetime,
) -> DeploymentAcceptanceReport:
    """为已停止或失败的部署生成确定性的v1验收报告。"""

    if record.status not in {DeploymentStatus.STOPPED, DeploymentStatus.FAILED}:
        raise ValueError("只有已停止或失败的部署可以生成验收报告")

    metrics = record.metrics
    checks: list[DeploymentAcceptanceCheck] = []
    terminal_ok = record.status is DeploymentStatus.STOPPED
    checks.append(
        _check(
            "runtime_terminal_state",
            "运行终态",
            AcceptanceCheckStatus.PASSED if terminal_ok else AcceptanceCheckStatus.FAILED,
            record.status.value,
            "安全停止（stopped）",
            detail=record.message,
        )
    )

    configured_fps = _metric(metrics, "configured_fps")
    actual_fps = _metric(metrics, "control_loop_actual_fps")
    if configured_fps and configured_fps > 0 and actual_fps is not None:
        ratio = actual_fps / configured_fps
        control_status = (
            AcceptanceCheckStatus.PASSED
            if ratio >= 0.9
            else AcceptanceCheckStatus.WARNING
            if ratio >= 0.75
            else AcceptanceCheckStatus.FAILED
        )
        checks.append(
            _check(
                "control_frequency",
                "控制频率",
                control_status,
                actual_fps,
                f">= {configured_fps * 0.9:.1f}",
                unit="FPS",
                detail=f"配置频率 {configured_fps:.1f} FPS，实测达成率 {ratio * 100:.1f}%",
            )
        )
    else:
        checks.append(
            _check(
                "control_frequency",
                "控制频率",
                AcceptanceCheckStatus.WARNING,
                actual_fps,
                "存在配置频率和实测控制频率",
                unit="FPS",
                detail="端侧没有提供完整的控制频率证据",
            )
        )

    control_p95 = _metric(metrics, "control_tick_p95_ms")
    if configured_fps and configured_fps > 0 and control_p95 is not None:
        frame_budget_ms = 1000.0 / configured_fps
        latency_status = (
            AcceptanceCheckStatus.PASSED
            if control_p95 <= frame_budget_ms
            else AcceptanceCheckStatus.WARNING
            if control_p95 <= frame_budget_ms * 1.5
            else AcceptanceCheckStatus.FAILED
        )
        checks.append(
            _check(
                "control_latency_p95",
                "控制周期 P95",
                latency_status,
                control_p95,
                f"<= {frame_budget_ms:.1f}",
                unit="ms",
                detail="P95超过单帧预算会导致控制节拍不稳定",
            )
        )
    else:
        checks.append(
            _check(
                "control_latency_p95",
                "控制周期 P95",
                AcceptanceCheckStatus.WARNING,
                control_p95,
                "存在P95周期与帧预算",
                unit="ms",
                detail="端侧没有提供完整的控制周期证据",
            )
        )

    deadline_misses = _metric(metrics, "control_deadline_misses")
    runtime_ticks = _metric(metrics, "runtime_ticks")
    if deadline_misses is not None and runtime_ticks is not None and runtime_ticks > 0:
        miss_ratio = deadline_misses / runtime_ticks
        deadline_status = (
            AcceptanceCheckStatus.PASSED
            if miss_ratio <= 0.05
            else AcceptanceCheckStatus.WARNING
            if miss_ratio <= 0.2
            else AcceptanceCheckStatus.FAILED
        )
        checks.append(
            _check(
                "control_deadlines",
                "控制超期比例",
                deadline_status,
                miss_ratio * 100,
                "<= 5",
                unit="%",
                detail=f"{deadline_misses:.0f}/{runtime_ticks:.0f} 个周期超期",
            )
        )

    actuator_faults = _metric(metrics, "actuator_faults")
    if actuator_faults is not None:
        checks.append(
            _check(
                "actuator_faults",
                "执行器故障",
                AcceptanceCheckStatus.PASSED
                if actuator_faults == 0
                else AcceptanceCheckStatus.FAILED,
                actuator_faults,
                "= 0",
                detail="运行期间不得出现执行器故障",
            )
        )

    commands_emitted = _metric(metrics, "motion_commands_emitted")
    commands_accepted = _metric(metrics, "motion_commands_accepted")
    if commands_emitted is not None and commands_emitted > 0:
        accepted = commands_accepted or 0.0
        command_ratio = accepted / commands_emitted
        command_status = (
            AcceptanceCheckStatus.PASSED
            if command_ratio >= 0.98
            else AcceptanceCheckStatus.WARNING
            if command_ratio >= 0.9
            else AcceptanceCheckStatus.FAILED
        )
        checks.append(
            _check(
                "motion_command_delivery",
                "运动命令接收率",
                command_status,
                command_ratio * 100,
                ">= 98",
                unit="%",
                detail=f"已接收 {accepted:.0f}/{commands_emitted:.0f} 条运动命令",
            )
        )
    elif commands_emitted is not None:
        checks.append(
            _check(
                "motion_command_delivery",
                "运动命令接收率",
                AcceptanceCheckStatus.NOT_APPLICABLE,
                0.0,
                "触发目标后进行判定",
                unit="%",
                detail="本次运行没有产生运动命令，不把未触发目标误判为系统故障",
            )
        )

    if _has_actuator(record):
        safe_stop = _metric(metrics, "actuator_safe_stop")
        checks.append(
            _check(
                "actuator_safe_stop",
                "执行器安全停止",
                AcceptanceCheckStatus.PASSED
                if safe_stop == 1.0
                else AcceptanceCheckStatus.FAILED
                if safe_stop is not None
                else AcceptanceCheckStatus.WARNING,
                safe_stop,
                "= 1",
                detail="终态必须确认执行器已进入安全停止状态",
            )
        )

    preview_age = _metric(metrics, "preview_age_latest_ms")
    if preview_age is not None:
        checks.append(
            _check(
                "preview_freshness",
                "预览帧龄",
                AcceptanceCheckStatus.PASSED
                if preview_age <= 500
                else AcceptanceCheckStatus.WARNING,
                preview_age,
                "<= 500",
                unit="ms",
                detail="预览仅用于观察，超限不会直接判定控制闭环失败",
            )
        )

    evidence = {
        name: float(value)
        for name, value in metrics.items()
        if name in _EVIDENCE_METRICS and math.isfinite(value)
    }
    statuses = {check.status for check in checks}
    if AcceptanceCheckStatus.FAILED in statuses:
        verdict = AcceptanceVerdict.FAILED
        summary = "运行未通过工程验收，请处理失败项后重新部署。"
    elif not evidence:
        verdict = AcceptanceVerdict.INCOMPLETE
        summary = "运行已结束，但缺少足够的端侧指标，暂不能完成工程验收。"
    elif AcceptanceCheckStatus.WARNING in statuses:
        verdict = AcceptanceVerdict.WARNING
        summary = "运行基本完成，但存在性能或证据告警，建议复核后再进入下一阶段。"
    else:
        verdict = AcceptanceVerdict.PASSED
        summary = "运行通过当前工程基线验收。"

    return DeploymentAcceptanceReport(
        attempt=record.attempt,
        verdict=verdict,
        summary=summary,
        generated_at=generated_at,
        checks=checks,
        evidence_metrics=evidence,
    )
