"""Анализатор: вердикты, дельты, события."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

from .config import Thresholds
from .models import CRITICAL_ATTR_NAMES, Event, Snapshot, Verdict


@dataclass
class Analysis:
    verdict: Verdict
    score: int
    reasons: list[str] = field(default_factory=list)
    deltas: dict[str, int] = field(default_factory=dict)
    events: list[Event] = field(default_factory=list)


def _get(row: Any, key: str) -> Any:
    try:
        return row[key]
    except (KeyError, IndexError, TypeError):
        return None


def analyze(snap: Snapshot, prev_row: Any, th: Thresholds, device_id: int | None = None) -> Analysis:
    """prev_row — строка предыдущего снимка из store (или None)."""
    verdict = Verdict.OK
    score = 0
    reasons: list[str] = []
    deltas: dict[str, int] = {}
    events: list[Event] = []

    def bump(v: Verdict, pts: int, reason: str) -> None:
        nonlocal verdict, score
        if v > verdict:
            verdict = v
        score += pts
        reasons.append(reason)

    # --- overall health / selftest ---
    if snap.smart_passed is False:
        bump(Verdict.REPLACE_NOW, 100, "SMART overall-health: FAILED")
        events.append(Event(device_id, "selftest_fail", "critical",
                            f"{snap.device.dev_node}: overall-health FAILED"))
    if snap.selftest_status and "fail" in snap.selftest_status.lower():
        bump(Verdict.REPLACE_NOW, 80, f"self-test: {snap.selftest_status}")
        events.append(Event(device_id, "selftest_fail", "critical",
                            f"{snap.device.dev_node}: self-test {snap.selftest_status}"))

    # --- дельты критичных атрибутов ---
    if prev_row is not None:
        for name in CRITICAL_ATTR_NAMES:
            old = _get(prev_row, name)
            new = getattr(snap, name)
            if old is not None and new is not None and new != old:
                deltas[name] = new - old

    # --- абсолютные значения критичных атрибутов ---
    if snap.reallocated is not None:
        if snap.reallocated >= th.reallocated_backup_now:
            bump(Verdict.BACKUP_NOW, 40, f"reallocated={snap.reallocated} (>= {th.reallocated_backup_now})")
        elif snap.reallocated >= th.reallocated_watch:
            bump(Verdict.BACKUP_SOON, 20, f"reallocated={snap.reallocated}")
        if deltas.get("reallocated", 0) > 0:
            bump(Verdict.BACKUP_NOW, 25, f"reallocated растёт: +{deltas['reallocated']}")
            events.append(Event(device_id, "attr_growth", "critical",
                                f"{snap.device.dev_node}: reallocated +{deltas['reallocated']} "
                                f"(итого {snap.reallocated})"))

    if snap.pending is not None:
        if snap.pending >= th.pending_backup_now:
            bump(Verdict.BACKUP_NOW, 50, f"pending={snap.pending} (>= {th.pending_backup_now})")
        elif snap.pending > 0:
            bump(Verdict.BACKUP_SOON, 30, f"pending={snap.pending}")
        if deltas.get("pending", 0) > 0:
            bump(Verdict.BACKUP_NOW, 30, f"pending растёт: +{deltas['pending']}")
            events.append(Event(device_id, "attr_growth", "critical",
                                f"{snap.device.dev_node}: pending +{deltas['pending']} "
                                f"(итого {snap.pending})"))

    if snap.uncorrectable is not None:
        if snap.uncorrectable >= th.uncorrectable_backup_now:
            bump(Verdict.BACKUP_NOW, 40, f"uncorrectable={snap.uncorrectable}")
        elif snap.uncorrectable > 0:
            bump(Verdict.BACKUP_SOON, 25, f"uncorrectable={snap.uncorrectable}")
        if deltas.get("uncorrectable", 0) > 0:
            bump(Verdict.BACKUP_NOW, 25, f"uncorrectable растёт: +{deltas['uncorrectable']}")
            events.append(Event(device_id, "attr_growth", "critical",
                                f"{snap.device.dev_node}: uncorrectable +{deltas['uncorrectable']} "
                                f"(итого {snap.uncorrectable})"))

    if snap.reported_uncorrect is not None and snap.reported_uncorrect > 0:
        bump(Verdict.BACKUP_SOON, 15, f"reported_uncorrect={snap.reported_uncorrect}")
        if deltas.get("reported_uncorrect", 0) > 0:
            bump(Verdict.BACKUP_NOW, 20, f"reported_uncorrect растёт: +{deltas['reported_uncorrect']}")
            events.append(Event(device_id, "attr_growth", "critical",
                                f"{snap.device.dev_node}: reported_uncorrect "
                                f"+{deltas['reported_uncorrect']} (итого {snap.reported_uncorrect})"))

    if snap.grown_defects is not None and snap.grown_defects > 0:
        if Verdict.BACKUP_SOON > verdict:
            reasons.append(f"grown_defects={snap.grown_defects}")
            score += 20

    # --- температура ---
    if snap.temperature is not None:
        if snap.temperature >= th.temp_critical:
            bump(Verdict.BACKUP_SOON, 15, f"temperature={snap.temperature}C (>= {th.temp_critical}C)")
        elif snap.temperature >= th.temp_warning:
            bump(Verdict.WATCH, 8, f"temperature={snap.temperature}C (>= {th.temp_warning}C)")

    # --- возраст / ресурс ---
    if snap.power_on_hours is not None and snap.power_on_hours >= th.power_on_hours_watch:
        bump(Verdict.WATCH, 10, f"power_on_hours={snap.power_on_hours}")
    if snap.load_cycle is not None and snap.load_cycle >= th.load_cycle_watch:
        bump(Verdict.WATCH, 8, f"load_cycle={snap.load_cycle}")

    # --- быстрый рост (относительный) ---
    if prev_row is not None:
        for name in ("end_to_end_error", "command_timeout"):
            old = _get(prev_row, name)
            new = getattr(snap, name)
            if old is not None and new is not None and new > old:
                bump(Verdict.BACKUP_SOON, 10, f"{name} растёт: {old} -> {new}")

    # смена вердикта — событие
    prev_verdict_str = _get(prev_row, "verdict") if prev_row is not None else None
    if prev_verdict_str:
        try:
            prev_verdict = Verdict.from_str(prev_verdict_str)
        except KeyError:
            prev_verdict = Verdict.OK
        if verdict > prev_verdict:
            events.append(Event(device_id, "verdict_change", "critical" if verdict >= Verdict.BACKUP_NOW else "warning",
                                f"{snap.device.dev_node}: {prev_verdict.label} -> {verdict.label}"))
        elif verdict < prev_verdict:
            events.append(Event(device_id, "verdict_change", "info",
                                f"{snap.device.dev_node}: {prev_verdict.label} -> {verdict.label}"))

    return Analysis(verdict=verdict, score=min(score, 100), reasons=reasons, deltas=deltas, events=events)


def reasons_from_row(row: Any) -> list[str]:
    raw = _get(row, "reasons")
    if not raw:
        return []
    try:
        return json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return []
