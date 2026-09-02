"""Движок бэктеста.

Методология, ради которой всё и затевалось:
  * сигнал считается на закрытии дня T, сделка исполняется по ОТКРЫТИЮ T+1;
  * издержки берутся пессимистично (комиссия + проскальзывание на каждую сделку);
  * стоп проверяется ежедневно по low; при гэпе вниз исполнение по открытию,
    а не по цене стопа — иначе бэктест врёт в свою пользу;
  * позиция округляется вниз до целого числа лотов;
  * свободные деньги лежат в денежном рынке и приносят ставку из конфига.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from indicators import atr, rsi, sma
from strategy import Signal, entry_candidates, is_rebalance_day, position_size, regime_risk_on


@dataclass
class Trade:
    secid: str
    entry_date: pd.Timestamp
    entry_price: float
    shares: int
    exit_date: pd.Timestamp | None = None
    exit_price: float | None = None
    reason: str = ""
    costs: float = 0.0

    @property
    def pnl(self) -> float:
        if self.exit_price is None:
            return 0.0
        return (self.exit_price - self.entry_price) * self.shares - self.costs

    @property
    def ret(self) -> float:
        base = self.entry_price * self.shares
        return self.pnl / base if base else 0.0


@dataclass
class Position:
    secid: str
    shares: int
    entry_price: float
    entry_date: pd.Timestamp
    stop: float
    bars_held: int = 0
    trade: Trade = field(default=None)


def build_features(prices: dict[str, pd.DataFrame], cfg: dict) -> dict[str, pd.DataFrame]:
    """Индикаторы по каждой бумаге. Считаются один раз, на скорректированных ценах."""
    e, s = cfg["entry"], cfg["sizing"]
    out = {}
    for secid, df in prices.items():
        if df.empty or len(df) < max(e["trend_sma"], e["rsi_period"], s["atr_period"]) + 5:
            continue
        f = df.copy()
        f["rsi"] = rsi(f["close"], e["rsi_period"])
        f["sma_trend"] = sma(f["close"], e["trend_sma"])
        f["atr"] = atr(f["high"], f["low"], f["close"], s["atr_period"])
        f["turnover_avg"] = f["value"].rolling(
            cfg["universe"]["turnover_window"],
            min_periods=cfg["universe"]["turnover_window"] // 2).mean()
        out[secid] = f
    return out


def liquid_universe(day: pd.Timestamp, features: dict[str, pd.DataFrame],
                    lots: dict[str, int], cfg: dict) -> list[str]:
    """Слой 2: кто достаточно ликвиден НА ЭТУ ДАТУ. Смотрим только назад."""
    u = cfg["universe"]
    max_lot_price = u.get("max_lot_price_rub")
    out = []
    for secid, f in features.items():
        if day not in f.index:
            continue
        row = f.loc[day]
        if pd.isna(row.get("turnover_avg")) or row["turnover_avg"] < u["min_avg_turnover_rub"]:
            continue
        if max_lot_price is not None:
            if row["close"] * lots.get(secid, 1) > max_lot_price:
                continue
        out.append(secid)
    return out


def _cash_rate(day: pd.Timestamp, cfg: dict) -> float:
    c = cfg["cash"]
    return float(c.get("by_year", {}).get(day.year, c["default_rate"]))


class Backtest:
    def __init__(self, prices: dict[str, pd.DataFrame], index_px: pd.DataFrame,
                 divs: dict[str, pd.DataFrame], lots: dict[str, int], cfg: dict):
        self.cfg = cfg
        self.prices = prices
        self.divs = divs
        self.lots = lots
        self.features = build_features(prices, cfg)
        self.index_close = index_px["close"]
        self.index_sma = sma(self.index_close, cfg["regime"]["sma"])
        self.cash = float(cfg["backtest"]["initial_capital"])
        self.positions: dict[str, Position] = {}
        self.trades: list[Trade] = []
        self.equity_curve: list[tuple[pd.Timestamp, float]] = []
        self._pending_buys: list[Signal] = []
        self._pending_sells: list[tuple[str, str]] = []
        self._universe_cache: tuple[pd.Timestamp, list[str]] | None = None

        bps = cfg["costs"]
        self.fee = bps["commission_bps"] / 10_000.0
        self.slip = bps["slippage_bps"] / 10_000.0

    # ------------------------------------------------------------- исполнение

    def _buy(self, day: pd.Timestamp, sig: Signal) -> None:
        f = self.features[sig.secid]
        if day not in f.index:
            return
        px_open = float(f.loc[day, "open"])
        if not np.isfinite(px_open) or px_open <= 0:
            return
        fill = px_open * (1 + self.slip)
        equity = self.equity(day)
        shares = position_size(equity, fill, sig.atr, self.lots.get(sig.secid, 1), self.cfg)
        if shares <= 0:
            return
        cost = shares * fill
        commission = cost * self.fee
        if cost + commission > self.cash:                      # не влезаем — режем
            lot = self.lots.get(sig.secid, 1)
            affordable = int(self.cash / (fill * (1 + self.fee)) // lot) * lot
            if affordable <= 0:
                return
            shares = affordable
            cost = shares * fill
            commission = cost * self.fee
        self.cash -= cost + commission
        trade = Trade(sig.secid, day, fill, shares, costs=commission)
        self.trades.append(trade)
        self.positions[sig.secid] = Position(
            secid=sig.secid, shares=shares, entry_price=fill, entry_date=day,
            stop=fill - self.cfg["sizing"]["atr_stop_mult"] * sig.atr, trade=trade)

    def _sell(self, day: pd.Timestamp, secid: str, price: float, reason: str) -> None:
        pos = self.positions.pop(secid, None)
        if pos is None:
            return
        fill = price * (1 - self.slip)
        proceeds = pos.shares * fill
        commission = proceeds * self.fee
        self.cash += proceeds - commission
        pos.trade.exit_date = day
        pos.trade.exit_price = fill
        pos.trade.reason = reason
        pos.trade.costs += commission

    def equity(self, day: pd.Timestamp) -> float:
        total = self.cash
        for secid, pos in self.positions.items():
            f = self.features[secid]
            if day in f.index:
                total += pos.shares * float(f.loc[day, "close"])
            else:
                total += pos.shares * pos.entry_price
        return total

    # ------------------------------------------------------------------- цикл

    def run(self) -> pd.DataFrame:
        cfg = self.cfg
        start = pd.Timestamp(cfg["backtest"]["start"])
        end = pd.Timestamp(cfg["backtest"]["end"])
        days = self.index_close.loc[start:end].index
        e, x = cfg["entry"], cfg["exit"]

        for i, day in enumerate(days):
            nxt = days[i + 1] if i + 1 < len(days) else None

            # 1. исполняем то, что решили вчера на закрытии — по открытию сегодня
            for secid, reason in self._pending_sells:
                f = self.features.get(secid)
                if secid in self.positions and f is not None and day in f.index:
                    self._sell(day, secid, float(f.loc[day, "open"]), reason)
            self._pending_sells = []
            for sig in self._pending_buys:
                if sig.secid not in self.positions and len(self.positions) < e["max_positions"]:
                    self._buy(day, sig)
            self._pending_buys = []

            # 2. стопы — работают каждый день, не раз в неделю
            for secid in list(self.positions):
                pos = self.positions[secid]
                f = self.features[secid]
                if day not in f.index or pos.entry_date == day:
                    continue
                row = f.loc[day]
                if float(row["low"]) <= pos.stop:
                    # гэп ниже стопа -> исполнение по открытию, а не по цене стопа
                    fill = min(float(row["open"]), pos.stop)
                    self._sell(day, secid, fill, "stop")
                else:
                    pos.bars_held += 1

            # 3. решения на закрытии — раз в неделю
            if is_rebalance_day(day, nxt):
                risk_on = regime_risk_on(self.index_close, self.index_sma, day)
                for secid in list(self.positions):
                    pos = self.positions[secid]
                    f = self.features[secid]
                    if day not in f.index:
                        continue
                    r = f.loc[day, "rsi"]
                    if not risk_on:
                        self._pending_sells.append((secid, "regime_off"))
                    elif pd.notna(r) and r > x["rsi_take"]:
                        self._pending_sells.append((secid, "take"))
                    elif pos.bars_held >= x["time_stop_days"]:
                        self._pending_sells.append((secid, "time_stop"))

                if risk_on:
                    leaving = {s for s, _ in self._pending_sells}
                    staying = set(self.positions) - leaving
                    free = e["max_positions"] - len(staying)
                    if free > 0:
                        uni = self._universe(day)
                        cands = entry_candidates(day, self.features, uni, self.divs, self.cfg)
                        # не откупаем то, из чего сегодня выходим — это лишний оборот
                        blocked = staying | leaving
                        self._pending_buys = [c for c in cands
                                              if c.secid not in blocked][:free]

            # 4. кэш работает
            self.cash *= (1 + _cash_rate(day, cfg) / 252)
            self.equity_curve.append((day, self.equity(day)))

        # закрываем хвосты по последней цене
        last = days[-1]
        for secid in list(self.positions):
            f = self.features[secid]
            if last in f.index:
                self._sell(last, secid, float(f.loc[last, "close"]), "end_of_test")

        return pd.DataFrame(self.equity_curve, columns=["date", "equity"]).set_index("date")

    def _universe(self, day: pd.Timestamp) -> list[str]:
        step = self.cfg["universe"]["rebuild_every_days"]
        if self._universe_cache and (day - self._universe_cache[0]).days < step:
            return self._universe_cache[1]
        uni = liquid_universe(day, self.features, self.lots, self.cfg)
        self._universe_cache = (day, uni)
        return uni
