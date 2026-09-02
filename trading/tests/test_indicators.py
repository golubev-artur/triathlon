"""Проверка индикаторов на каноническом наборе Уайлдера."""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
import pandas as pd
from indicators import rsi, atr, sma

WILDER_CLOSES = [44.34, 44.09, 44.15, 43.61, 44.33, 44.83, 45.10, 45.42, 45.84,
                 46.08, 45.89, 46.03, 45.61, 46.28, 46.28, 46.00, 46.03, 46.41,
                 46.22, 45.64]


def test_rsi_matches_wilder():
    got = rsi(pd.Series(WILDER_CLOSES), 14).dropna().round(2).tolist()
    expected = [70.46, 66.25, 66.48, 69.35, 66.29, 57.92]
    assert got == expected, f"RSI разошёлся: {got}"


def test_rsi_all_up_is_100():
    s = pd.Series(range(1, 40), dtype=float)
    assert rsi(s, 14).dropna().iloc[-1] == 100.0


def test_atr_positive_and_lagged():
    n = 60
    close = pd.Series([100 + i * 0.5 for i in range(n)])
    high, low = close + 1.0, close - 1.0
    a = atr(high, low, close, 14)
    assert a.iloc[:14].isna().all(), "ATR не должен считаться на неполном окне"
    assert (a.dropna() > 0).all()


def test_sma_no_lookahead():
    s = pd.Series(range(30), dtype=float)
    m = sma(s, 10)
    assert m.iloc[9] == sum(range(10)) / 10
    assert m.iloc[:9].isna().all()


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_"):
            fn(); print(f"  ok  {name}")
    print("индикаторы: все проверки пройдены")
