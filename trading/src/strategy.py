"""Правила стратегии. Никакого состояния — только «что бы я сделал в этот день».

Все сигналы считаются по данным на закрытие дня T.
Исполнение — по открытию T+1 (см. backtest.py). Это принципиально:
сигнал на закрытии пятницы нельзя исполнить по цене этой же пятницы.
"""
from __future__ import annotations

from dataclasses import dataclass

import pandas as pd


@dataclass(frozen=True)
class Signal:
    secid: str
    rsi: float
    atr: float
    close: float


def is_rebalance_day(day: pd.Timestamp, next_day: pd.Timestamp | None) -> bool:
    """Последний торговый день недели. Не «пятница» — пятница бывает выходной."""
    if next_day is None:
        return False
    return next_day.isocalendar()[1] != day.isocalendar()[1] or next_day.year != day.year


def regime_risk_on(index_close: pd.Series, index_sma: pd.Series, day: pd.Timestamp) -> bool:
    """Слой 1: разрешены ли лонги вообще."""
    if day not in index_close.index:
        return False
    c, m = index_close.loc[day], index_sma.loc[day]
    if pd.isna(c) or pd.isna(m):
        return False
    return bool(c > m)


def days_to_next_dividend(divs: pd.DataFrame, day: pd.Timestamp) -> int | None:
    """Календарных дней до ближайшей отсечки. None — отсечек впереди нет."""
    if divs is None or divs.empty:
        return None
    future = divs.loc[divs["ex_date"] >= day, "ex_date"]
    if future.empty:
        return None
    return int((future.iloc[0] - day).days)


def entry_candidates(day: pd.Timestamp, features: dict[str, pd.DataFrame],
                     universe: list[str], divs: dict[str, pd.DataFrame],
                     cfg: dict) -> list[Signal]:
    """Слой 3: кто проходит фильтр входа. Возвращает отсортированных кандидатов."""
    e = cfg["entry"]
    out: list[Signal] = []
    for secid in universe:
        df = features.get(secid)
        if df is None or day not in df.index:
            continue
        row = df.loc[day]
        if pd.isna(row.get("rsi")) or pd.isna(row.get("sma_trend")) or pd.isna(row.get("atr")):
            continue
        if row["rsi"] >= e["rsi_max"]:
            continue
        if row["close"] <= row["sma_trend"]:          # не ловим падающие ножи
            continue
        if row["atr"] <= 0:
            continue
        d = days_to_next_dividend(divs.get(secid), day)
        if d is not None and d <= e["days_before_dividend"]:
            continue
        out.append(Signal(secid, float(row["rsi"]), float(row["atr"]), float(row["close"])))
    out.sort(key=lambda s: s.rsi)                      # чем перепроданнее, тем выше
    return out


def position_size(equity: float, price: float, atr_value: float, lot: int,
                  cfg: dict) -> int:
    """Слой 4: размер позиции от риска, а не «поровну».

    Риск на сделку фиксирован (2% капитала). Стоп стоит на 2*ATR ниже входа.
    Значит объём = риск_в_рублях / расстояние_до_стопа. По волатильной бумаге
    позиция автоматически меньше.
    """
    s = cfg["sizing"]
    risk_rub = equity * s["risk_per_trade"]
    stop_dist = s["atr_stop_mult"] * atr_value
    if stop_dist <= 0 or price <= 0:
        return 0
    shares = risk_rub / stop_dist
    cap = equity * s["max_weight"] / price             # потолок на бумагу
    shares = min(shares, cap)
    lots = int(shares // lot)
    return max(lots, 0) * lot
