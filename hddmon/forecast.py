"""Прогноз: линейная регрессия по критичным атрибутам → ETA."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from statistics import linear_regression

from .models import CRITICAL_ATTR_IDS, KEY_ATTRS

# Целевые пороги «достаточно плохо» для ETA
ETA_TARGETS = {
    "reallocated": 100,
    "pending": 20,
    "uncorrectable": 10,
    "reported_uncorrect": 50,
    "grown_defects": 100,
}


@dataclass
class Trend:
    column: str
    slope_per_day: float
    r_threshold: float  # порог, к которому идёт тренд
    current: int
    eta_date: datetime | None  # None = не наступит / за пределами горизонта
    eta_days: float | None


def _linear_eta(xs: list[float], ys: list[float]) -> tuple[float, float] | None:
    if len(xs) < 3:
        return None
    if xs[0] == xs[-1]:
        return None
    try:
        lr = linear_regression(xs, ys)
    except ValueError:
        return None
    return lr.slope, lr.intercept


def _eta_days_to(slope: float, intercept: float, x_last: float, y_last: float, target: float) -> float | None:
    if slope <= 0:
        return None
    if y_last >= target:
        return 0.0
    x_eta = (target - intercept) / slope
    days = x_eta - x_last
    if days < 0 or days > 365 * 10:
        return None
    return days


def predict_trends(series: list[tuple[datetime, int]], column: str) -> Trend | None:
    """series: [(taken_at, value)] по возрастанию времени."""
    if len(series) < 3:
        return None
    t0 = series[0][0]
    xs = [(t - t0).total_seconds() / 86400.0 for t, _ in series]
    ys = [float(v) for _, v in series]
    reg = _linear_eta(xs, ys)
    if reg is None:
        return None
    slope, intercept = reg
    target = ETA_TARGETS.get(column, 0)
    eta = _eta_days_to(slope, intercept, xs[-1], ys[-1], target)
    eta_date = t0 + timedelta(days=xs[-1] + eta) if eta is not None else None
    return Trend(
        column=column,
        slope_per_day=slope,
        r_threshold=target,
        current=int(ys[-1]),
        eta_date=eta_date,
        eta_days=eta,
    )


def predict_device(series_by_column: dict[str, list[tuple[datetime, int]]]) -> list[Trend]:
    out: list[Trend] = []
    for column, series in series_by_column.items():
        trend = predict_trends(series, column)
        if trend is not None and (trend.slope_per_day > 0 or trend.eta_days is not None):
            out.append(trend)
    out.sort(key=lambda t: (t.eta_days is None, t.eta_days if t.eta_days is not None else 0))
    return out


def critical_columns() -> list[str]:
    return [KEY_ATTRS[i] for i in CRITICAL_ATTR_IDS] + ["end_to_end_error", "command_timeout"]
