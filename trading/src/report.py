"""Метрики и сравнение с бенчмарками.

Бенчмарков два и оба обязательны:
  * MCFTR — индекс МосБиржи полной доходности (с дивидендами). Обгонять его —
    минимальное условие, иначе проще купить индексный фонд.
  * денежный рынок — безрисковая альтернатива. При ставке 16-19% это не
    формальность, а прямой конкурент.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

TRADING_DAYS = 252


def _cagr(curve: pd.Series) -> float:
    if len(curve) < 2 or curve.iloc[0] <= 0:
        return 0.0
    years = (curve.index[-1] - curve.index[0]).days / 365.25
    return (curve.iloc[-1] / curve.iloc[0]) ** (1 / years) - 1 if years > 0 else 0.0


def _max_drawdown(curve: pd.Series) -> float:
    peak = curve.cummax()
    return float((1 - curve / peak).max())


def _sharpe(curve: pd.Series, rf_daily: pd.Series | float = 0.0) -> float:
    r = curve.pct_change().dropna()
    if isinstance(rf_daily, pd.Series):
        r = r - rf_daily.reindex(r.index).fillna(0.0)
    else:
        r = r - rf_daily
    sd = r.std()
    return float(r.mean() / sd * np.sqrt(TRADING_DAYS)) if sd > 0 else 0.0


def cash_curve(index: pd.DatetimeIndex, cfg: dict, start_value: float) -> pd.Series:
    c = cfg["cash"]
    rates = pd.Series([c.get("by_year", {}).get(d.year, c["default_rate"]) for d in index],
                      index=index) / TRADING_DAYS
    return start_value * (1 + rates).cumprod()


def trade_stats(trades: list) -> dict:
    closed = [t for t in trades if t.exit_price is not None]
    if not closed:
        return {"trades": 0}
    rets = np.array([t.ret for t in closed])
    wins, losses = rets[rets > 0], rets[rets <= 0]
    gross_win = float(sum(t.pnl for t in closed if t.pnl > 0))
    gross_loss = float(-sum(t.pnl for t in closed if t.pnl <= 0))
    reasons = pd.Series([t.reason for t in closed]).value_counts().to_dict()
    return {
        "trades": len(closed),
        "win_rate": float(len(wins) / len(closed)),
        "avg_win": float(wins.mean()) if len(wins) else 0.0,
        "avg_loss": float(losses.mean()) if len(losses) else 0.0,
        "profit_factor": gross_win / gross_loss if gross_loss > 0 else float("inf"),
        "total_costs": float(sum(t.costs for t in closed)),
        "exits": reasons,
    }


def summarize(equity: pd.Series, trades: list, cfg: dict,
              benchmark: pd.Series | None = None) -> dict:
    idx = equity.index
    cash = cash_curve(idx, cfg, float(equity.iloc[0]))
    rf_daily = cash.pct_change().fillna(0.0)
    res = {
        "period": f"{idx[0].date()} — {idx[-1].date()}",
        "start_capital": float(equity.iloc[0]),
        "end_capital": float(equity.iloc[-1]),
        "cagr": _cagr(equity),
        "sharpe": _sharpe(equity, rf_daily),
        "max_drawdown": _max_drawdown(equity),
        "cash_cagr": _cagr(cash),
    }
    res["excess_over_cash"] = res["cagr"] - res["cash_cagr"]
    if benchmark is not None and len(benchmark.dropna()) > 1:
        b = benchmark.reindex(idx).ffill().dropna()
        if len(b) > 1:
            b = b / b.iloc[0] * float(equity.iloc[0])
            res["benchmark_cagr"] = _cagr(b)
            res["benchmark_max_drawdown"] = _max_drawdown(b)
            res["excess_over_benchmark"] = res["cagr"] - res["benchmark_cagr"]
    res.update(trade_stats(trades))
    return res


def check_acceptance(res: dict, cfg: dict) -> tuple[bool, list[str]]:
    """Критерии зафиксированы в конфиге ДО теста. Не проходит — стратегия отвергается."""
    a = cfg["acceptance"]
    checks = [
        ("сделок >= %d" % a["min_trades"], res.get("trades", 0) >= a["min_trades"]),
        ("Sharpe > %.2f" % a["min_sharpe"], res.get("sharpe", 0) > a["min_sharpe"]),
        ("просадка < %.0f%%" % (a["max_drawdown"] * 100),
         res.get("max_drawdown", 1) < a["max_drawdown"]),
        ("превышение над денежным рынком > %.0f п.п." % (a["min_excess_over_cash"] * 100),
         res.get("excess_over_cash", -1) > a["min_excess_over_cash"]),
    ]
    failed = [name for name, ok in checks if not ok]
    return (not failed), [f"{'ПРОЙДЕН' if ok else 'ПРОВАЛЕН'}: {n}" for n, ok in checks]


def render(res: dict, cfg: dict, title: str) -> str:
    passed, lines = check_acceptance(res, cfg)
    pct = lambda v: f"{v * 100:+.2f}%" if isinstance(v, float) else str(v)
    out = [f"\n{'=' * 62}", f"  {title}", "=" * 62,
           f"  Период:                {res['period']}",
           f"  Капитал:               {res['start_capital']:,.0f} -> {res['end_capital']:,.0f} ₽",
           f"  Доходность (CAGR):     {pct(res['cagr'])}",
           f"  Денежный рынок:        {pct(res['cash_cagr'])}",
           f"  Превышение над кэшем:  {pct(res['excess_over_cash'])}"]
    if "benchmark_cagr" in res:
        out += [f"  Индекс полной дох.:    {pct(res['benchmark_cagr'])}",
                f"  Превышение над инд.:   {pct(res['excess_over_benchmark'])}"]
    out += [f"  Sharpe:                {res['sharpe']:.2f}",
            f"  Макс. просадка:        {res['max_drawdown'] * 100:.1f}%",
            "-" * 62,
            f"  Сделок:                {res.get('trades', 0)}",
            f"  Прибыльных:            {res.get('win_rate', 0) * 100:.1f}%",
            f"  Средняя прибыль:       {pct(res.get('avg_win', 0.0))}",
            f"  Средний убыток:        {pct(res.get('avg_loss', 0.0))}",
            f"  Profit factor:         {res.get('profit_factor', 0):.2f}",
            f"  Издержки всего:        {res.get('total_costs', 0):,.0f} ₽",
            f"  Причины выхода:        {res.get('exits', {})}",
            "-" * 62, "  КРИТЕРИИ ПРИЁМКИ:"]
    out += [f"    {l}" for l in lines]
    out += ["", f"  ИТОГ: {'СТРАТЕГИЯ ПРИНИМАЕТСЯ' if passed else 'СТРАТЕГИЯ ОТВЕРГАЕТСЯ'}",
            "=" * 62]
    return "\n".join(out)
