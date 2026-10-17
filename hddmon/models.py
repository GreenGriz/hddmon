"""Единая модель данных: устройства, атрибуты, снимки, вердикты."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import IntEnum


class Verdict(IntEnum):
    OK = 0
    WATCH = 1
    BACKUP_SOON = 2
    BACKUP_NOW = 3
    REPLACE_NOW = 4

    @property
    def label(self) -> str:
        return self.name

    @classmethod
    def from_str(cls, s: str) -> "Verdict":
        return cls[s.upper()]


EXIT_CODES = {
    Verdict.OK: 0,
    Verdict.WATCH: 10,
    Verdict.BACKUP_SOON: 20,
    Verdict.BACKUP_NOW: 30,
    Verdict.REPLACE_NOW: 40,
}


# Ключевые ATA-атрибуты: id -> имя колонки в snapshots
KEY_ATTRS: dict[int, str] = {
    5: "reallocated",
    197: "pending",
    198: "uncorrectable",
    187: "reported_uncorrect",
    184: "end_to_end_error",
    188: "command_timeout",
    193: "load_cycle",
    12: "power_cycle",
    9: "power_on_hours",
    194: "temperature",
}

# Критичные для динамики (дельты и регрессия)
CRITICAL_ATTR_IDS = (5, 197, 198, 187)
CRITICAL_ATTR_NAMES = tuple(KEY_ATTRS[i] for i in CRITICAL_ATTR_IDS)


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat(timespec="seconds")


@dataclass
class DeviceInfo:
    dev_node: str
    model: str | None = None
    model_family: str | None = None
    serial: str | None = None
    transport: str | None = None
    protocol: str | None = None  # ata | scsi | nvme
    capacity_bytes: int | None = None


@dataclass
class Attribute:
    attr_id: int | None
    name: str
    raw_value: int | None = None
    normalized: int | None = None
    worst: int | None = None
    failed: bool | None = None


@dataclass
class Snapshot:
    device: DeviceInfo
    taken_at: datetime = field(default_factory=utcnow)
    smart_passed: bool | None = None
    temperature: int | None = None
    power_on_hours: int | None = None
    reallocated: int | None = None
    pending: int | None = None
    uncorrectable: int | None = None
    reported_uncorrect: int | None = None
    end_to_end_error: int | None = None
    command_timeout: int | None = None
    load_cycle: int | None = None
    power_cycle: int | None = None
    grown_defects: int | None = None  # SCSI
    selftest_status: str | None = None
    attributes: list[Attribute] = field(default_factory=list)
    raw_json: str | None = None


@dataclass
class Event:
    device_id: int | None
    type: str  # verdict_change | attr_growth | selftest_fail | device_missing | device_new
    severity: str  # info | warning | critical
    message: str
    at: datetime = field(default_factory=utcnow)
