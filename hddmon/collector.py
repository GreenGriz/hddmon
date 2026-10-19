"""Коллектор: обнаружение дисков и чтение smartctl -j -a."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from dataclasses import dataclass

from .config import Config


class CollectorError(RuntimeError):
    pass


@dataclass
class RawDevice:
    dev_node: str
    type_hint: str | None  # из --scan: ata/scsi/nvme/…, если есть
    json_data: dict
    smartctl_rc: int
    used_type: str | None  # какой -d реально сработал (None = авто)


def _smartctl_base(cfg: Config) -> list[str]:
    exe = shutil.which(cfg.smartctl_path) or cfg.smartctl_path
    if os.geteuid() == 0:
        return [exe]
    if cfg.use_sudo and shutil.which("sudo"):
        return ["sudo", "-n", exe]
    return [exe]


def _run_smartctl(base: list[str], args: list[str], timeout: int = 60) -> subprocess.CompletedProcess:
    try:
        proc = subprocess.run(
            [*base, *args],
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired as e:
        raise CollectorError(f"smartctl timed out: {' '.join(args)}") from e
    except FileNotFoundError as e:
        raise CollectorError(f"smartctl not found: {base[-1]}") from e
    if base and "sudo" in os.path.basename(base[0]) and proc.returncode != 0 \
            and "password is required" in (proc.stderr or ""):
        raise CollectorError(
            "sudo требует пароль: запусти от root, настрой sudoers "
            "(NOPASSWD: /usr/sbin/smartctl) или systemd unit от root"
        )
    return proc


def scan_devices(cfg: Config) -> list[dict]:
    base = _smartctl_base(cfg)
    proc = _run_smartctl(base, ["--scan", "-j"])
    if proc.returncode & 1:  # bit 0: ошибка командной строки
        raise CollectorError(f"smartctl --scan failed: {proc.stderr.strip() or proc.stdout.strip()}")
    try:
        data = json.loads(proc.stdout)
    except json.JSONDecodeError as e:
        raise CollectorError(f"smartctl --scan: invalid JSON: {e}") from e
    devices = data.get("devices", [])
    if not devices:
        raise CollectorError("smartctl --scan: no devices found")
    return devices


def _type_fallbacks(type_hint: str | None) -> list[str | None]:
    order: list[str | None] = [None]  # сначала автоопределение
    if type_hint and type_hint not in ("auto",):
        order.append(type_hint)
    for t in ("ata", "scsi", "nvme"):
        if t not in order:
            order.append(t)
    return order


def _json_error(data: dict) -> str | None:
    """Извлекает сообщение об ошибке из JSON smartctl, если данных нет."""
    if _has_any_data(data):
        return None
    sc = data.get("smartctl", {})
    messages = sc.get("messages") or []
    errs = [m.get("string", "") for m in messages if m.get("severity") == "error"]
    if errs:
        return "; ".join(errs)
    if "exit_status" in sc:
        return f"exit_status={sc['exit_status']}"
    return None


def collect_device(cfg: Config, dev_node: str, type_hint: str | None = None) -> RawDevice:
    base = _smartctl_base(cfg)
    last_err = ""
    for dtype in _type_fallbacks(type_hint):
        args = ["-j", "-a"]
        if dtype:
            args += ["-d", dtype]
        args.append(dev_node)
        proc = _run_smartctl(base, args)
        rc = proc.returncode
        # bit 0/1/2 = фатальные ошибки запуска/доступа/команды без JSON
        if rc & 0b111 and not proc.stdout.strip():
            last_err = proc.stderr.strip() or f"rc={rc}"
            if "Permission denied" in (proc.stderr + proc.stdout):
                raise CollectorError(
                    f"{dev_node}: Permission denied — запусти от root, настрой sudoers "
                    f"или systemd unit от root (см. SPEC §7)"
                )
            continue
        if not proc.stdout.strip():
            last_err = f"empty output, rc={rc}"
            continue
        try:
            data = json.loads(proc.stdout)
        except json.JSONDecodeError:
            last_err = f"invalid JSON, rc={rc}"
            continue
        if not isinstance(data, dict):
            last_err = f"unexpected JSON shape, rc={rc}"
            continue
        err = _json_error(data)
        if err is not None:
            if "Permission denied" in err:
                raise CollectorError(
                    f"{dev_node}: Permission denied — запусти от root, настрой sudoers "
                    f"(NOPASSWD: /usr/sbin/smartctl) или systemd unit от root"
                )
            last_err = err
            continue
        return RawDevice(dev_node=dev_node, type_hint=type_hint, json_data=data, smartctl_rc=rc, used_type=dtype)
    raise CollectorError(f"{dev_node}: all device types failed ({last_err})")


def _has_any_data(data: dict) -> bool:
    return any(k in data for k in ("ata_smart_attributes", "scsi", "nvme", "smart_status", "model_name"))


def collect_all(cfg: Config) -> tuple[list[RawDevice], list[str], list[str]]:
    """Возвращает (успешные снимки, ошибки по устройствам, обнаруженные узлы).

    Обнаруженные узлы включают и те, что не удалось прочитать: ошибка чтения
    не означает, что диск исчез.
    """
    results: list[RawDevice] = []
    errors: list[str] = []
    discovered: list[str] = []
    for dev in scan_devices(cfg):
        name = dev.get("name", "")
        hint = dev.get("type")
        if not name:
            continue
        discovered.append(name)
        try:
            results.append(collect_device(cfg, name, hint))
        except CollectorError as e:
            errors.append(str(e))
    return results, errors, discovered
