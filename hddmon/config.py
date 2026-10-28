"""Конфигурация: TOML-файл + дефолты."""

from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

CONFIG_SEARCH_PATHS = [
    Path("hddmon.toml"),
    Path(os.environ.get("HDDMON_CONFIG", "")),
    Path.home() / ".config" / "hddmon" / "config.toml",
    Path("/etc/hddmon/config.toml"),
]


@dataclass
class Thresholds:
    temp_warning: int = 45
    temp_critical: int = 55
    power_on_hours_watch: int = 43200  # 5 лет
    load_cycle_watch: int = 600000
    pending_backup_now: int = 10
    reallocated_watch: int = 1
    reallocated_backup_now: int = 20
    uncorrectable_backup_now: int = 5
    reported_uncorrect_backup_now: int = 50
    grow_pct_per_year_watch: float = 10.0  # рост критичного атрибута, %/год от текущего значения — WATCH


@dataclass
class TelegramConfig:
    enabled: bool = False
    token: str = ""
    chat_id: str = ""


@dataclass
class EmailConfig:
    enabled: bool = False
    smtp_host: str = ""
    smtp_port: int = 587
    starttls: bool = True
    use_ssl: bool = False  # true для SMTP_SSL, обычно порт 465
    username: str = ""
    password: str = ""
    from_addr: str = ""
    to_addrs: list[str] = field(default_factory=list)


@dataclass
class Config:
    db_path: str = ""
    store_raw_json: bool = False
    smartctl_path: str = "smartctl"
    use_sudo: bool = True
    thresholds: Thresholds = field(default_factory=Thresholds)
    telegram: TelegramConfig = field(default_factory=TelegramConfig)
    email: EmailConfig = field(default_factory=EmailConfig)

    def __post_init__(self) -> None:
        if not self.db_path:
            base = os.environ.get("XDG_DATA_HOME") or str(Path.home() / ".local" / "share")
            self.db_path = str(Path(base) / "hddmon" / "hddmon.db")


def _merge(section: object, data: dict) -> None:
    for key, value in data.items():
        if hasattr(section, key):
            current = getattr(section, key)
            if isinstance(current, bool) and isinstance(value, bool):
                setattr(section, key, value)
            elif isinstance(current, int) and isinstance(value, (int, float)) and not isinstance(value, bool):
                setattr(section, key, int(value))
            elif isinstance(current, float) and isinstance(value, (int, float)):
                setattr(section, key, float(value))
            elif isinstance(current, str) and isinstance(value, str):
                setattr(section, key, value)
            elif isinstance(current, list) and isinstance(value, list):
                setattr(section, key, [str(v) for v in value])
            else:
                setattr(section, key, value)


def load_config(explicit_path: str | None = None) -> Config:
    cfg = Config()
    path: Path | None = None
    if explicit_path:
        path = Path(explicit_path)
        if not path.is_file():
            raise FileNotFoundError(f"config not found: {path}")
    else:
        for candidate in CONFIG_SEARCH_PATHS:
            if str(candidate) and candidate.is_file():
                path = candidate
                break
    if path is not None:
        with open(path, "rb") as f:
            data = tomllib.load(f)
        if "db_path" in data:
            cfg.db_path = os.path.expanduser(str(data["db_path"]))
        if "store_raw_json" in data:
            cfg.store_raw_json = bool(data["store_raw_json"])
        if "smartctl_path" in data:
            cfg.smartctl_path = str(data["smartctl_path"])
        if "use_sudo" in data:
            cfg.use_sudo = bool(data["use_sudo"])
        for section_name, section in (
            ("thresholds", cfg.thresholds),
            ("telegram", cfg.telegram),
            ("email", cfg.email),
        ):
            if section_name in data and isinstance(data[section_name], dict):
                _merge(section, data[section_name])
    return cfg
