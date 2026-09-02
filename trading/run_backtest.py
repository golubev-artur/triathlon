#!/usr/bin/env python3
"""Прогон стратегии на истории МосБиржи с walk-forward разделением.

    python3 run_backtest.py                # обе выборки
    python3 run_backtest.py --oos-only     # только out-of-sample
    python3 run_backtest.py --no-cache     # перекачать данные

Правило, ради которого разделение и сделано: параметры подбираются ТОЛЬКО на
in-sample (до 2021 года). Результат на out-of-sample смотрится один раз.
Подкрутили параметр после взгляда на OOS — тест сгорел, начинайте с новой гипотезы.
"""
from __future__ import annotations

import argparse
import os
import sys
from datetime import date, datetime

import pandas as pd
import yaml

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "src"))

import moex
from backtest import Backtest
from report import render, summarize


def load_cfg(path: str) -> dict:
    with open(path, encoding="utf-8") as f:
        return yaml.safe_load(f)


def _d(s: str) -> date:
    return datetime.fromisoformat(str(s)).date()


def load_market(cfg: dict, use_cache: bool = True):
    bt = cfg["backtest"]
    start, end = _d(bt["start"]), _d(bt["end"])
    # запас в 1 год до старта — на прогрев SMA(200) и ATR
    warm = date(start.year - 1, start.month, start.day)

    print(f"Состав доски {cfg['universe']['board']} по историческим срезам...")
    tickers = moex.universe_candidates(warm, end, cfg["universe"]["board"], cache=use_cache)
    print(f"  кандидатов за период (включая делистнутые): {len(tickers)}")

    prices, divs = {}, {}
    for i, sec in enumerate(tickers, 1):
        try:
            px = moex.history(sec, warm, end, cfg["universe"]["board"], cache=use_cache)
        except Exception as exc:
            print(f"  ! {sec}: {exc}")
            continue
        if px.empty or len(px) < 250:
            continue
        try:
            dv = moex.dividends(sec, cache=use_cache)
        except Exception:
            dv = pd.DataFrame(columns=["ex_date", "value"])
        prices[sec] = moex.adjust_for_dividends(px, dv)   # цены БЕЗ дивгэпов
        divs[sec] = dv
        if i % 25 == 0:
            print(f"  загружено {i}/{len(tickers)}...")
    print(f"  бумаг с пригодной историей: {len(prices)}")

    lots = moex.lot_sizes(sorted(prices), cfg["universe"]["board"], cache=use_cache)
    index_px = moex.index_history(cfg["regime"]["index"], warm, end, cache=use_cache)
    try:
        total_return = moex.index_history("MCFTR", warm, end, cache=use_cache)["close"]
    except Exception:
        total_return = None
    return prices, index_px, divs, lots, total_return


def run_window(prices, index_px, divs, lots, cfg, start, end, title, benchmark):
    window = dict(cfg)
    window["backtest"] = {**cfg["backtest"], "start": str(start), "end": str(end)}
    bt = Backtest(prices, index_px, divs, lots, window)
    equity = bt.run()["equity"]
    if equity.empty:
        print(f"{title}: нет данных в окне")
        return None
    res = summarize(equity, bt.trades, window, benchmark)
    print(render(res, window, title))
    return res, equity, bt.trades


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", default=os.path.join(os.path.dirname(__file__), "config.yaml"))
    ap.add_argument("--oos-only", action="store_true")
    ap.add_argument("--no-cache", action="store_true")
    ap.add_argument("--out", default=os.path.join(os.path.dirname(__file__), "results"))
    args = ap.parse_args()

    cfg = load_cfg(args.config)
    prices, index_px, divs, lots, tr_index = load_market(cfg, use_cache=not args.no_cache)
    if not prices or index_px.empty:
        print("Данные не загрузились — проверьте доступ к iss.moex.com")
        return 1

    os.makedirs(args.out, exist_ok=True)
    b = cfg["backtest"]
    outputs = []

    if not args.oos_only:
        outputs.append(("in_sample", run_window(
            prices, index_px, divs, lots, cfg, b["start"], b["in_sample_end"],
            "IN-SAMPLE (здесь можно подбирать параметры)", tr_index)))

    oos_start = (pd.Timestamp(b["in_sample_end"]) + pd.Timedelta(days=1)).date()
    outputs.append(("out_of_sample", run_window(
        prices, index_px, divs, lots, cfg, oos_start, b["end"],
        "OUT-OF-SAMPLE (единственный результат, которому можно верить)", tr_index)))

    for name, payload in outputs:
        if not payload:
            continue
        res, equity, trades = payload
        equity.to_csv(os.path.join(args.out, f"equity_{name}.csv"))
        rows = [{"secid": t.secid, "entry_date": t.entry_date, "entry_price": t.entry_price,
                 "shares": t.shares, "exit_date": t.exit_date, "exit_price": t.exit_price,
                 "reason": t.reason, "pnl": t.pnl, "ret": t.ret, "costs": t.costs}
                for t in trades]
        pd.DataFrame(rows).to_csv(os.path.join(args.out, f"trades_{name}.csv"), index=False)
    print(f"\nРезультаты сохранены в {args.out}/")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
