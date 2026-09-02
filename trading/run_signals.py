#!/usr/bin/env python3
"""Список сигналов на ближайшую сделку по тем же правилам, что в бэктесте.

    python3 run_signals.py                    # состояние рынка + кандидаты
    python3 run_signals.py --capital 15000    # с расчётом размера позиции

Скрипт НЕ отправляет заявок и не требует торгового токена — только читает
котировки MOEX ISS. Сигналы считаются на закрытии последнего торгового дня,
исполнять их следует по открытию следующего.
"""
from __future__ import annotations

import argparse
import os
import sys
from datetime import date, timedelta

import pandas as pd
import yaml

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "src"))

import moex
from backtest import build_features, liquid_universe
from indicators import sma
from strategy import entry_candidates, position_size


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", default=os.path.join(os.path.dirname(__file__), "config.yaml"))
    ap.add_argument("--capital", type=float, default=None,
                    help="капитал для расчёта размера позиции, ₽")
    ap.add_argument("--no-cache", action="store_true")
    args = ap.parse_args()

    with open(args.config, encoding="utf-8") as f:
        cfg = yaml.safe_load(f)
    use_cache = not args.no_cache

    end = date.today()
    start = end - timedelta(days=500)          # хватает на SMA(200) с запасом

    index_px = moex.index_history(cfg["regime"]["index"], start, end, cache=use_cache)
    if index_px.empty:
        print("Не удалось получить индекс — проверьте доступ к iss.moex.com")
        return 1
    idx_close = index_px["close"]
    idx_sma = sma(idx_close, cfg["regime"]["sma"])
    day = idx_close.index[-1]
    risk_on = bool(idx_close.iloc[-1] > idx_sma.iloc[-1])

    print(f"\nДата расчёта: {day.date()}")
    print(f"{cfg['regime']['index']}: {idx_close.iloc[-1]:,.0f}  "
          f"SMA({cfg['regime']['sma']}): {idx_sma.iloc[-1]:,.0f}")
    print(f"РЕЖИМ: {'РИСК-ОН — лонги разрешены' if risk_on else 'РИСК-ОФФ — только денежный рынок'}\n")
    if not risk_on:
        print("Действие: позиций не открывать, капитал держать в фонде денежного рынка.")
        return 0

    tickers = moex.universe_candidates(end - timedelta(days=90), end,
                                       cfg["universe"]["board"], cache=use_cache)
    prices, divs = {}, {}
    for sec in tickers:
        try:
            px = moex.history(sec, start, end, cfg["universe"]["board"], cache=use_cache)
            if px.empty or len(px) < 250:
                continue
            dv = moex.dividends(sec, cache=use_cache)
            prices[sec] = moex.adjust_for_dividends(px, dv)
            divs[sec] = dv
        except Exception:
            continue

    features = build_features(prices, cfg)
    lots = moex.lot_sizes(sorted(features), cfg["universe"]["board"], cache=use_cache)
    uni = liquid_universe(day, features, lots, cfg)
    cands = entry_candidates(day, features, uni, divs, cfg)

    print(f"Ликвидная вселенная: {len(uni)} бумаг")
    if not cands:
        print("Кандидатов по фильтру нет. Действие: ничего не делать.\n")
        return 0

    print(f"Кандидаты (берём первые {cfg['entry']['max_positions']}):\n")
    header = f"{'тикер':<8}{'RSI':>7}{'цена':>10}{'ATR':>8}{'стоп':>10}{'лот':>6}"
    if args.capital:
        header += f"{'шт.':>7}{'сумма ₽':>11}"
    print(header)
    print("-" * len(header))
    mult = cfg["sizing"]["atr_stop_mult"]
    for c in cands[: cfg["entry"]["max_positions"] * 2]:
        lot = lots.get(c.secid, 1)
        stop = c.close - mult * c.atr
        line = f"{c.secid:<8}{c.rsi:>7.1f}{c.close:>10.2f}{c.atr:>8.2f}{stop:>10.2f}{lot:>6}"
        if args.capital:
            n = position_size(args.capital, c.close, c.atr, lot, cfg)
            line += f"{n:>7}{n * c.close:>11,.0f}"
        print(line)
    print("\nИсполнять по открытию следующего торгового дня. Заявки ставите вы.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
