"""Прогноз: линейная регрессия по критичным атрибутам → ETA."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from statistics import linear_regression

from .config import Thresholds
from .models import CRITICAL_ATTR_IDS, KEY_ATTRS

# Целевые пороги «достаточно плохо» для ETA (перекрываются конфигом)
ETA_TARGETS: dict[str, float] = {
    "reallocated": 100,
    "pending": 20,
    "uncorrectable": 10,
    "reported_uncorrect": 50,
    "grown_defects": 100,
}

# Минимальный r², при котором тренду можно верить
MIN_R2 = 0.5


@dataclass
class Trend:
    column: str
    slope_per_day: float
    r_threshold: float | None  # порог, к которому идёт тренд
    current: int
    eta_date: datetime | None  # None = не наступит / за пределами горизонта
    eta_days: float | None
    r2: float = 0.0  # качество линейной регрессии


def targets_from_thresholds(th: Thresholds) -> dict[str, float]:
    return {
        "reallocated": float(th.reallocated_backup_now),
        "pending": float(th.pending_backup_now),
        "uncorrectable": float(th.uncorrectable_backup_now),
        "reported_uncorrect": float(th.reported_uncorrect_backup_now),
    }


def _linear_fit(xs: list[float], ys: list[float]) -> tuple[float, float, float] | None:
    if len(xs) < 3:
        return None
    if xs[0] == xs[-1]:
        return None
    try:
        lr = linear_regression(xs, ys)
    except ValueError:
        return None
    mean_y = sum(ys) / len(ys)
    ss_tot = sum((y - mean_y) ** 2 for y in ys)
    ss_res = sum((y - (lr.intercept + lr.slope * x)) ** 2 for x, y in zip(xs, ys))
    r2 = 1.0 if ss_tot == 0 else max(0.0, 1.0 - ss_res / ss_tot)
    return lr.slope, lr.intercept, r2


def _eta_days_to(slope: float, intercept: float, x_last: float, y_last: float,
                 target: float, r2: float) -> float | None:
    if y_last >= target:
        return 0.0
    if slope <= 0 or r2 < MIN_R2:
        return None
    x_eta = (target - intercept) / slope
    days = x_eta - x_last
    if days < 0 or days > 365 * 10:
        return None
    return days


def predict_trends(series: list[tuple[datetime, int]], column: str,
                   targets: dict[str, float] | None = None) -> Trend | None:
    """series: [(taken_at, value)] по возрастанию времени."""
    if len(series) < 3:
        return None
    t0 = series[0][0]
    xs = [(t - t0).total_seconds() / 86400.0 for t, _ in series]
    ys = [float(v) for _, v in series]
    fit = _linear_fit(xs, ys)
    if fit is None:
        return None
    slope, intercept, r2 = fit
    all_targets = dict(ETA_TARGETS)
    if targets:
        all_targets.update(targets)
    target = all_targets.get(column)
    eta = None
    if target is not None:
        eta = _eta_days_to(slope, intercept, xs[-1], ys[-1], target, r2)
    eta_date = t0 + timedelta(days=xs[-1] + eta) if eta is not None else None
    return Trend(
        column=column,
        slope_per_day=slope,
        r_threshold=target,
        current=int(ys[-1]),
        eta_date=eta_date,
        eta_days=eta,
        r2=r2,
    )


def predict_device(series_by_column: dict[str, list[tuple[datetime, int]]],
                   targets: dict[str, float] | None = None) -> list[Trend]:
    out: list[Trend] = []
    for column, series in series_by_column.items():
        trend = predict_trends(series, column, targets=targets)
        if trend is not None and (trend.slope_per_day > 0 or trend.eta_days is not None):
            out.append(trend)
    out.sort(key=lambda t: (t.eta_days is None, t.eta_days if t.eta_days is not None else 0))
    return out


def critical_columns() -> list[str]:
    return [KEY_ATTRS[i] for i in CRITICAL_ATTR_IDS] + ["end_to_end_error", "command_timeout"]
