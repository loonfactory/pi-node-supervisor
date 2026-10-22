"""Common capture targets and assessment of measured sensor timing."""

from __future__ import annotations

import time
from typing import Any

from .runtime_v2 import RunRecordV2


def capture_target_ns(lead_seconds: float) -> int:
    """Use the same Linux boot clock as libcamera SensorTimestamp."""
    return time.clock_gettime_ns(time.CLOCK_BOOTTIME) + int(lead_seconds * 1e9)


def assess_synchronization(
    runs: tuple[RunRecordV2, ...],
    *,
    target_ns: int,
    device_ids: set[str],
    max_skew_milliseconds: float,
) -> dict[str, Any]:
    timestamps = {
        run.device_id: run.result["sensorTimestampNs"]
        for run in runs
        if run.status == "succeeded"
        and run.result is not None
        and run.result.get("syncReady") is True
        and isinstance(run.result.get("sensorTimestampNs"), int)
        and run.result["sensorTimestampNs"] >= target_ns
    }
    complete = set(timestamps) == device_ids
    skew = (max(timestamps.values()) - min(timestamps.values())) / 1e6 if complete else None
    return {
        "captureAtNs": target_ns,
        "runIds": [run.run_id for run in runs],
        "sensorTimestampsNs": timestamps,
        "skewMilliseconds": skew,
        "maxSkewMilliseconds": max_skew_milliseconds,
        "status": (
            "incomplete" if not complete
            else "aligned" if skew <= max_skew_milliseconds
            else "excessiveSkew"
        ),
    }
