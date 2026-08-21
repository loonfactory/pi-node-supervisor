from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Protocol

from .errors import AlarmConflictError, RTCError, ValidationError
from .timeutil import ensure_utc


class RTC(Protocol):
    def check(self) -> list[dict[str, str]]: ...
    def read_time(self) -> datetime: ...
    def read_alarm(self) -> int | None: ...
    def set_alarm(
        self, target: datetime, *, now: datetime, minimum_lead_seconds: int,
        owned_alarm_epoch: int | None,
    ) -> int: ...
    def clear_alarm(self, *, owned_alarm_epoch: int | None) -> None: ...


@dataclass(slots=True)
class SysfsRTC:
    root: Path = Path("/sys/class/rtc/rtc0")

    @property
    def alarm_path(self) -> Path:
        return self.root / "wakealarm"

    def check(self) -> list[dict[str, str]]:
        checks: list[dict[str, str]] = []
        checks.append(self._path_check("rtc_device", self.root, os.R_OK))
        checks.append(self._path_check("wakealarm_read", self.alarm_path, os.R_OK))
        checks.append(self._path_check("wakealarm_write", self.alarm_path, os.W_OK))
        time_path = self.root / "since_epoch"
        if not time_path.exists():
            time_path = self.root / "time"
        checks.append(self._path_check("rtc_time", time_path, os.R_OK))
        name_path = self.root / "name"
        if name_path.exists():
            try:
                name = name_path.read_text(encoding="ascii").strip()
                driver = name.split(maxsplit=1)[0] if name else ""
                checks.append({
                    "name": "rtc_driver",
                    "status": "pass" if driver == "rpi-rtc" else "fail",
                    "detail": name,
                })
            except OSError as exc:
                checks.append({"name": "rtc_driver", "status": "fail", "detail": str(exc)})
        else:
            checks.append({"name": "rtc_driver", "status": "fail", "detail": f"missing: {name_path}"})
        return checks

    @staticmethod
    def _path_check(name: str, path: Path, mode: int) -> dict[str, str]:
        if not path.exists():
            return {"name": name, "status": "fail", "detail": f"missing: {path}"}
        if not os.access(path, mode):
            return {"name": name, "status": "fail", "detail": f"permission denied: {path}"}
        return {"name": name, "status": "pass", "detail": str(path)}

    def read_time(self) -> datetime:
        epoch_path = self.root / "since_epoch"
        try:
            if epoch_path.exists():
                epoch = int(epoch_path.read_text(encoding="ascii").strip())
                return datetime.fromtimestamp(epoch, UTC)
            date_value = (self.root / "date").read_text(encoding="ascii").strip()
            time_value = (self.root / "time").read_text(encoding="ascii").strip()
            return datetime.fromisoformat(f"{date_value}T{time_value}+00:00").astimezone(UTC)
        except (OSError, ValueError) as exc:
            raise RTCError("read_time", str(self.root), exc) from exc

    def read_alarm(self) -> int | None:
        try:
            raw = self.alarm_path.read_text(encoding="ascii").strip()
            if not raw or raw == "0":
                return None
            value = int(raw)
            return value if value > 0 else None
        except (OSError, ValueError) as exc:
            raise RTCError("read_alarm", str(self.alarm_path), exc) from exc

    def set_alarm(
        self,
        target: datetime,
        *,
        now: datetime,
        minimum_lead_seconds: int,
        owned_alarm_epoch: int | None,
    ) -> int:
        target = ensure_utc(target, field="wake_at")
        now = ensure_utc(now, field="now")
        target_epoch = int(target.timestamp())
        if target_epoch - int(now.timestamp()) < minimum_lead_seconds:
            raise ValidationError(
                f"wake alarm must be at least {minimum_lead_seconds} seconds in the future"
            )
        current = self.read_alarm()
        if current is not None and current != owned_alarm_epoch:
            raise AlarmConflictError(
                "set_alarm", str(self.alarm_path),
                f"existing alarm {current} is not owned by pi-power-manager",
            )
        if current == target_epoch:
            return target_epoch
        try:
            if current is not None:
                self.alarm_path.write_text("0", encoding="ascii")
            self.alarm_path.write_text(str(target_epoch), encoding="ascii")
        except OSError as exc:
            raise RTCError("write_alarm", str(self.alarm_path), exc) from exc
        actual = self.read_alarm()
        if actual != target_epoch:
            raise RTCError(
                "verify_alarm", str(self.alarm_path),
                f"requested {target_epoch}, read back {actual}",
            )
        return target_epoch

    def clear_alarm(self, *, owned_alarm_epoch: int | None) -> None:
        current = self.read_alarm()
        if current is None:
            return
        if current != owned_alarm_epoch:
            raise AlarmConflictError(
                "clear_alarm", str(self.alarm_path),
                f"existing alarm {current} is not owned by pi-power-manager",
            )
        try:
            self.alarm_path.write_text("0", encoding="ascii")
        except OSError as exc:
            raise RTCError("clear_alarm", str(self.alarm_path), exc) from exc
        if self.read_alarm() is not None:
            raise RTCError("verify_clear_alarm", str(self.alarm_path), "alarm remains set")
