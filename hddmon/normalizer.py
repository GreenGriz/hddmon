"""Нормализация smartctl JSON (ATA/SCSI/NVMe) -> Snapshot."""

from __future__ import annotations

import json
from typing import Any

from .collector import RawDevice
from .models import Attribute, DeviceInfo, KEY_ATTRS, Snapshot


def _pick(data: dict, *keys: str) -> Any:
    for k in keys:
        if k in data and data[k] is not None:
            return data[k]
    return None


def _as_int(value: Any) -> int | None:
    if value is None:
        return None
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value)
    if isinstance(value, str):
        try:
            return int(value.strip(), 0)
        except ValueError:
            return None
    return None


def _raw_int(raw: Any) -> int | None:
    if isinstance(raw, dict):
        return _as_int(raw.get("value"))
    return _as_int(raw)


def _detect_protocol(data: dict) -> str:
    if "nvme" in data:
        return "nvme"
    if "ata_smart_attributes" in data or "ata_smart_data" in data:
        return "ata"
    if "scsi" in data:
        return "scsi"
    dev_type = str(_pick(data.get("device", {}), "type") or "").lower()
    if "nvme" in dev_type:
        return "nvme"
    if "scsi" in dev_type or "usb" in dev_type:
        return "scsi"
    if "ata" in dev_type or "sat" in dev_type:
        return "ata"
    return "unknown"


def _device_info(data: dict, dev_node: str, protocol: str) -> DeviceInfo:
    dev = data.get("device", {}) or {}
    capacity = _pick(data, "user_capacity", "nvme_total_capacity")
    cap_bytes = None
    if isinstance(capacity, dict):
        cap_bytes = _as_int(capacity.get("bytes"))
    return DeviceInfo(
        dev_node=dev_node,
        model=_pick(data, "model_name", "product", "nvme_model_number"),
        model_family=_pick(data, "model_family", "nvme_firmware"),
        serial=_pick(data, "serial_number", "nvme_serial_number"),
        transport=_pick(dev, "protocol") or _pick(dev, "type"),
        protocol=protocol,
        capacity_bytes=cap_bytes,
    )


def _selftest_status(data: dict, protocol: str) -> str | None:
    if protocol == "ata":
        st = _pick(data.get("ata_smart_data", {}), "self_test")
        if isinstance(st, dict):
            status = st.get("status")
            if isinstance(status, dict):
                return status.get("string") or status.get("value")
    elif protocol == "scsi":
        scsi = data.get("scsi", {}) or {}
        st = scsi.get("self_test")
        if isinstance(st, dict):
            status = st.get("status")
            if isinstance(status, dict):
                return status.get("string") or status.get("value")
    elif protocol == "nvme":
        nvme = data.get("nvme", {}) or {}
        st = nvme.get("self_test_result")
        if isinstance(st, dict):
            return st.get("string")
        if isinstance(st, list) and st:
            last = st[-1]
            if isinstance(last, dict):
                return last.get("string")
    return None


def _normalize_ata(data: dict, snap: Snapshot) -> None:
    table = ((data.get("ata_smart_attributes") or {}).get("table")) or []
    for item in table:
        attr_id = _as_int(item.get("id"))
        raw = _raw_int(item.get("raw"))
        attr = Attribute(
            attr_id=attr_id,
            name=str(item.get("name") or f"attr_{attr_id}"),
            raw_value=raw,
            normalized=_as_int(item.get("value")),
            worst=_as_int(item.get("worst")),
            failed=bool(item["raw"].get("string", "").upper() == "FAILING_NOW") if isinstance(item.get("raw"), dict) else None,
        )
        snap.attributes.append(attr)
        col = KEY_ATTRS.get(attr_id) if attr_id is not None else None
        if col and raw is not None:
            if col == "temperature":
                # 194: младший байт raw = температура
                snap.temperature = raw & 0xFF
            else:
                setattr(snap, col, raw)
    pot = data.get("power_on_time") or {}
    if snap.power_on_hours is None:
        snap.power_on_hours = _as_int(pot.get("hours"))


def _normalize_scsi(data: dict, snap: Snapshot) -> None:
    scsi = data.get("scsi", {}) or {}

    def add(name: str, value: Any) -> None:
        iv = _as_int(value)
        snap.attributes.append(Attribute(attr_id=None, name=name, raw_value=iv))

    grown = _as_int(_pick(scsi, "grown_defects", "grown_defect_count"))
    snap.grown_defects = grown
    snap.reallocated = grown
    add("grown_defects", grown)

    temp = scsi.get("temperature")
    if isinstance(temp, dict):
        t = _as_int(temp.get("current"))
        if t is not None:
            snap.temperature = t
            add("temperature", t)
    elif temp is not None:
        snap.temperature = _as_int(temp)
        add("temperature", temp)

    poh = scsi.get("power_on_hours")
    poh_val = _as_int(poh.get("value") if isinstance(poh, dict) else poh)
    snap.power_on_hours = poh_val
    add("power_on_hours", poh_val)

    ssc = scsi.get("start_stop_cycle_counter")
    if isinstance(ssc, dict):
        snap.power_cycle = _as_int(ssc.get("value"))
        add("start_stop_cycle_counter", snap.power_cycle)

    for err_key, label in (
        ("read_errors", "read_errors"),
        ("write_errors", "write_errors"),
        ("verify_errors", "verify_errors"),
    ):
        block = scsi.get(err_key)
        if isinstance(block, dict):
            total = _as_int(block.get("total_errors"))
            add(label, total)
            if total:
                snap.reported_uncorrect = (snap.reported_uncorrect or 0) + total

    for k, v in scsi.items():
        if k in ("verification", "temperature", "power_on_hours", "start_stop_cycle_counter",
                 "read_errors", "write_errors", "verify_errors", "self_test"):
            continue
        if isinstance(v, (int, str)) and k not in ("vendor", "product", "revision", "protection_type"):
            iv = _as_int(v)
            if iv is not None:
                add(k, iv)


def _normalize_nvme(data: dict, snap: Snapshot) -> None:
    nvme = data.get("nvme", {}) or {}

    def add(name: str, value: Any) -> None:
        iv = _as_int(value)
        snap.attributes.append(Attribute(attr_id=None, name=name, raw_value=iv))

    cw = nvme.get("critical_warning")
    cw_val = _as_int(cw.get("value") if isinstance(cw, dict) else cw)
    add("critical_warning", cw_val)

    temp = nvme.get("temperature")
    if isinstance(temp, dict):
        t = _as_int(temp.get("current"))
        if t is not None:
            snap.temperature = t
            add("temperature", t)

    poh = nvme.get("power_on_hours")
    poh_val = _as_int(poh.get("value") if isinstance(poh, dict) else poh)
    snap.power_on_hours = poh_val
    add("power_on_hours", poh_val)

    pu = nvme.get("percentage_used")
    add("percentage_used", pu.get("value") if isinstance(pu, dict) else pu)

    spare = nvme.get("available_spare")
    add("available_spare", spare.get("value") if isinstance(spare, dict) else spare)

    media = nvme.get("media_errors")
    media_val = _as_int(media.get("value") if isinstance(media, dict) else media)
    snap.uncorrectable = media_val
    add("media_errors", media_val)

    spare_thresh = nvme.get("available_spare_threshold")
    add("available_spare_threshold", spare_thresh.get("value") if isinstance(spare_thresh, dict) else spare_thresh)

    for k in ("controller_busy_time", "power_cycles", "unsafe_shutdowns",
              "num_err_log_entries", "warning_temp_time", "critical_comp_time"):
        if k in nvme:
            v = nvme[k]
            val = _as_int(v.get("value") if isinstance(v, dict) else v)
            add(k, val)
            if k == "power_cycles" and val is not None:
                snap.power_cycle = val


def normalize(raw: RawDevice, store_raw: bool = False) -> Snapshot:
    data = raw.json_data
    protocol = _detect_protocol(data)
    snap = Snapshot(device=_device_info(data, raw.dev_node, protocol))

    status = data.get("smart_status")
    if isinstance(status, dict) and "passed" in status:
        snap.smart_passed = bool(status["passed"])
    elif raw.smartctl_rc & 0b1000:  # bit 3: DISK FAILING
        snap.smart_passed = False

    temp = data.get("temperature")
    if isinstance(temp, dict) and snap.temperature is None:
        snap.temperature = _as_int(temp.get("current"))
    elif isinstance(temp, (int, float)) and snap.temperature is None:
        snap.temperature = _as_int(temp)

    if protocol == "ata":
        _normalize_ata(data, snap)
    elif protocol == "scsi":
        _normalize_scsi(data, snap)
    elif protocol == "nvme":
        _normalize_nvme(data, snap)

    # temperature сверху может перекрыть атрибут 194 — лучше свежий общий показатель
    if isinstance(temp, dict) and temp.get("current") is not None:
        snap.temperature = _as_int(temp.get("current"))

    snap.selftest_status = _selftest_status(data, protocol)
    if store_raw:
        snap.raw_json = json.dumps(data, ensure_ascii=False)
    return snap
