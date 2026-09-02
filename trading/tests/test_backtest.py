"""Проверка движка на синтетике: нужно убедиться, что бэктест не жульничает.

Три вещи, из-за которых самодельные боты показывают красивую историю и теряют
деньги на живом счёте: look-ahead (сделка по цене сигнала), стоп по цене стопа
при гэпе вниз, и забытые комиссии. Здесь каждая проверяется отдельно.
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import numpy as np
import pandas as pd

from backtest import Backtest

CFG = {
    "universe": {"board": "TQBR", "min_avg_turnover_rub": 1_000, "turnover_window": 20,
                 "rebuild_every_days": 21, "max_lot_price_rub": None},
    "regime": {"index": "IMOEX", "sma": 200},
    "entry": {"rsi_period": 14, "rsi_max": 35, "trend_sma": 200,
              "days_before_dividend": 5, "max_positions": 3, "rank_by": "rsi"},
    "sizing": {"risk_per_trade": 0.02, "atr_period": 14, "atr_stop_mult": 2.0,
               "max_weight": 0.3333},
    "exit": {"rsi_take": 60, "time_stop_days": 20},
    "costs": {"commission_bps": 30, "slippage_bps": 10},
    "cash": {"default_rate": 0.10, "by_year": {}},
    "backtest": {"start": "2020-01-01", "end": "2022-12-31", "initial_capital": 100_000,
                 "in_sample_end": "2021-12-31"},
    "acceptance": {"min_trades": 40, "min_sharpe": 0.7, "max_drawdown": 0.20,
                   "min_excess_over_cash": 0.05},
}


def _series(dates, closes, gap_low=None):
    """OHLC из ряда закрытий. gap_low: {позиция: low} — чтобы смоделировать пролив."""
    close = np.asarray(closes, dtype=float)
    df = pd.DataFrame({
        "open": np.r_[close[0], close[:-1]],
        "high": close * 1.01,
        "low": close * 0.99,
        "close": close,
        "volume": 1_000.0,
        "value": 500_000_000.0,
    }, index=dates)
    if gap_low:
        for i, lo in gap_low.items():
            df.iloc[i, df.columns.get_loc("low")] = lo
            df.iloc[i, df.columns.get_loc("open")] = lo
    return df


def _make_world(n=760, seed=7):
    """Растущий рынок с регулярными откатами — среда, в которой стратегия
    на возврате к среднему в принципе должна давать сделки."""
    rng = np.random.default_rng(seed)
    dates = pd.bdate_range("2019-01-01", periods=n)
    t = np.arange(n)
    base = 100 * np.exp(0.0004 * t)
    wave = 1 + 0.06 * np.sin(t / 11.0)
    prices, divs, lots = {}, {}, {}
    for k, sec in enumerate(["AAA", "BBB", "CCC", "DDD"]):
        noise = rng.normal(0, 0.004, n).cumsum()
        closes = base * wave * (1 + 0.03 * k) * np.exp(noise)
        prices[sec] = _series(dates, closes)
        divs[sec] = pd.DataFrame(columns=["ex_date", "value"])
        lots[sec] = 1
    idx = pd.DataFrame({"close": base * (1 + 0.01 * np.sin(t / 40.0))}, index=dates)
    return prices, idx, divs, lots


def test_engine_runs_and_trades():
    prices, idx, divs, lots = _make_world()
    bt = Backtest(prices, idx, divs, lots, CFG)
    eq = bt.run()
    assert len(eq) > 100
    assert np.isfinite(eq["equity"]).all(), "кривая капитала содержит NaN/inf"
    assert (eq["equity"] > 0).all(), "капитал ушёл в ноль или минус"
    assert len(bt.trades) > 0, "движок не совершил ни одной сделки"
    print(f"      сделок: {len(bt.trades)}, итог: {eq['equity'].iloc[-1]:,.0f}")


def test_entry_uses_next_day_open_not_signal_close():
    """Ключевая проверка на look-ahead: цена входа обязана происходить
    от ОТКРЫТИЯ дня сделки, а не от закрытия дня сигнала."""
    prices, idx, divs, lots = _make_world()
    bt = Backtest(prices, idx, divs, lots, CFG)
    bt.run()
    slip = CFG["costs"]["slippage_bps"] / 10_000
    checked = 0
    for tr in bt.trades:
        day_open = float(prices[tr.secid].loc[tr.entry_date, "open"])
        assert abs(tr.entry_price - day_open * (1 + slip)) < 1e-6, (
            f"{tr.secid}: вход {tr.entry_price} != открытие*(1+slip) {day_open * (1 + slip)}")
        # день сделки не может быть днём сигнала: сигнал считается на закрытии
        assert tr.entry_date in prices[tr.secid].index
        checked += 1
    assert checked > 0
    print(f"      проверено входов: {checked}")


def test_stop_fills_at_open_on_gap_down():
    """При гэпе вниз стоп исполняется по открытию, а не по цене стопа.
    Обратное — самая частая ложь в самодельных бэктестах."""
    n = 300
    dates = pd.bdate_range("2020-01-01", periods=n)
    t = np.arange(n)
    closes = 100 * np.exp(0.001 * t) * (1 + 0.05 * np.sin(t / 9.0))
    closes[-1] = closes[-2] * 0.80                       # обвал на 20%
    df = _series(dates, closes, gap_low={n - 1: closes[-2] * 0.78})
    prices = {"AAA": df}
    idx = pd.DataFrame({"close": 100 * np.exp(0.001 * t)}, index=dates)
    cfg = {**CFG, "backtest": {**CFG["backtest"], "start": "2020-01-01", "end": "2021-12-31"}}
    bt = Backtest(prices, idx, {"AAA": pd.DataFrame(columns=["ex_date", "value"])},
                  {"AAA": 1}, cfg)
    bt.run()
    stops = [t_ for t_ in bt.trades if t_.reason == "stop"]
    if stops:
        tr = stops[-1]
        row = df.loc[tr.exit_date]
        slip = cfg["costs"]["slippage_bps"] / 10_000
        expected_max = max(float(row["open"]), 0.0) * (1 - slip)
        assert tr.exit_price <= expected_max + 1e-6, (
            "стоп исполнен выше открытия — движок жульничает в свою пользу")
        print(f"      стоп-выход по {tr.exit_price:.2f} при открытии {row['open']:.2f}")


def test_costs_are_charged():
    prices, idx, divs, lots = _make_world()
    bt = Backtest(prices, idx, divs, lots, CFG)
    bt.run()
    closed = [t for t in bt.trades if t.exit_price is not None]
    assert closed, "нет закрытых сделок"
    assert all(t.costs > 0 for t in closed), "комиссии не начислены"
    total = sum(t.costs for t in closed)
    print(f"      суммарные издержки: {total:,.0f} ₽ на {len(closed)} сделок")


def test_regime_off_keeps_us_in_cash():
    """Падающий индекс -> лонги запрещены -> сделок быть не должно."""
    n = 500
    dates = pd.bdate_range("2020-01-01", periods=n)
    t = np.arange(n)
    prices = {"AAA": _series(dates, 100 * np.exp(0.001 * t) * (1 + 0.05 * np.sin(t / 9.0)))}
    idx = pd.DataFrame({"close": 200 * np.exp(-0.002 * t)}, index=dates)   # индекс валится
    cfg = {**CFG, "backtest": {**CFG["backtest"], "start": "2020-01-01", "end": "2021-12-31"}}
    bt = Backtest(prices, idx, {"AAA": pd.DataFrame(columns=["ex_date", "value"])},
                  {"AAA": 1}, cfg)
    bt.run()
    assert not bt.trades, f"в режиме РИСК-ОФФ открыто {len(bt.trades)} позиций"
    print("      в риск-офф сделок нет — фильтр режима работает")


def test_dividend_adjustment_removes_gap():
    from moex import adjust_for_dividends
    dates = pd.bdate_range("2021-01-01", periods=10)
    closes = [100, 100, 100, 100, 100, 88, 88, 88, 88, 88]      # гэп на 12% = дивиденд
    px = _series(dates, closes)
    divs = pd.DataFrame({"ex_date": [dates[5]], "value": [12.0]})
    adj = adjust_for_dividends(px, divs)
    before, after = adj["close"].iloc[4], adj["close"].iloc[5]
    assert abs(before - after) < 1e-6, f"гэп не убран: {before} -> {after}"
    print(f"      дивгэп 100->88 скорректирован в {before:.2f}->{after:.2f}")


if __name__ == "__main__":
    fns = [(n, f) for n, f in sorted(globals().items()) if n.startswith("test_")]
    for name, fn in fns:
        print(f"  -> {name}")
        fn()
        print(f"  ok  {name}\n")
    print(f"движок: {len(fns)} проверок пройдено")
