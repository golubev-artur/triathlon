"""Технические индикаторы. Всё считается только по прошлым данным (без look-ahead)."""
import numpy as np
import pandas as pd


def _wilder(values: np.ndarray, period: int) -> np.ndarray:
    """Сглаживание Уайлдера: затравка = SMA первых `period` значений,
    далее avg = (avg*(n-1) + x)/n. Именно так считаются RSI и ATR в оригинале."""
    out = np.full(values.shape, np.nan, dtype=float)
    if len(values) < period:
        return out
    seed = np.nanmean(values[:period])
    out[period - 1] = seed
    prev = seed
    for i in range(period, len(values)):
        x = values[i]
        if np.isnan(x):
            x = 0.0
        prev = (prev * (period - 1) + x) / period
        out[i] = prev
    return out


def sma(s: pd.Series, period: int) -> pd.Series:
    return s.rolling(period, min_periods=period).mean()


def rsi(close: pd.Series, period: int = 14) -> pd.Series:
    delta = close.diff()
    gain = delta.clip(lower=0.0).fillna(0.0).to_numpy()
    loss = (-delta).clip(lower=0.0).fillna(0.0).to_numpy()
    # первая дельта отсутствует -> считаем со второго элемента
    avg_gain = np.concatenate([[np.nan], _wilder(gain[1:], period)])
    avg_loss = np.concatenate([[np.nan], _wilder(loss[1:], period)])
    with np.errstate(divide="ignore", invalid="ignore"):
        rs = np.where(avg_loss > 0, avg_gain / avg_loss, np.nan)
        out = 100.0 - 100.0 / (1.0 + rs)
    # падений не было вовсе -> RSI = 100
    out = np.where((avg_loss == 0) & ~np.isnan(avg_gain), 100.0, out)
    return pd.Series(out, index=close.index)


def atr(high: pd.Series, low: pd.Series, close: pd.Series, period: int = 14) -> pd.Series:
    prev_close = close.shift(1)
    tr = pd.concat(
        [high - low, (high - prev_close).abs(), (low - prev_close).abs()], axis=1
    ).max(axis=1)
    vals = np.concatenate([[np.nan], _wilder(tr.fillna(0.0).to_numpy()[1:], period)])
    return pd.Series(vals, index=close.index)
