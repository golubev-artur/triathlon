"""Загрузка данных с MOEX ISS с кэшированием на диск.

Три вещи, ради которых этот модуль вообще существует:
  1. Исторический состав торгуемых бумаг на каждую дату — чтобы во вселенную
     попадали и те, кого потом делистнули (иначе survivorship bias).
  2. Корректировка цен на дивиденды — иначе бэктест увидит десятки «обвалов»,
     которых не было, и стратегия на откатах будет покупать дивгэпы.
  3. Размер лота — без него расчёт позиции на малом счёте отрывается от реальности.
"""
from __future__ import annotations

import json
import os
import time
from datetime import date, timedelta

import pandas as pd
import requests

ISS = "https://iss.moex.com/iss"
SHARES = f"{ISS}/history/engines/stock/markets/shares/boards"
INDEX = f"{ISS}/history/engines/stock/markets/index/securities"
CACHE = os.path.join(os.path.dirname(__file__), "..", "data")

_session = requests.Session()
_session.headers.update({"User-Agent": "moex-backtest/1.0"})


def _get(url: str, params: dict | None = None, retries: int = 4) -> dict:
    params = dict(params or {})
    params["iss.meta"] = "off"
    delay = 2.0
    for attempt in range(retries):
        try:
            r = _session.get(url, params=params, timeout=30)
            r.raise_for_status()
            return r.json()
        except Exception:
            if attempt == retries - 1:
                raise
            time.sleep(delay)
            delay *= 2
    raise RuntimeError("unreachable")


def _block(payload: dict, name: str) -> pd.DataFrame:
    blk = payload.get(name, {})
    return pd.DataFrame(blk.get("data", []), columns=blk.get("columns", []))


def _paged(url: str, block: str, params: dict | None = None) -> pd.DataFrame:
    """ISS отдаёт по 100 строк, ходим постранично через &start=."""
    frames, start = [], 0
    while True:
        p = dict(params or {})
        p["start"] = start
        df = _block(_get(url, p), block)
        if df.empty:
            break
        frames.append(df)
        if len(df) < 100:
            break
        start += len(df)
    return pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()


def _cache_path(name: str) -> str:
    os.makedirs(CACHE, exist_ok=True)
    return os.path.join(CACHE, name)


# ---------------------------------------------------------------- вселенная

def traded_securities(on: date, board: str = "TQBR") -> list[str]:
    """Тикеры, реально торговавшиеся на указанную дату."""
    df = _paged(f"{SHARES}/{board}/securities.json", "history", {"date": on.isoformat()})
    if df.empty or "SECID" not in df:
        return []
    return sorted(df["SECID"].dropna().unique().tolist())


def universe_candidates(start: date, end: date, board: str = "TQBR",
                        step_days: int = 90, cache: bool = True) -> list[str]:
    """Объединение составов доски по срезам раз в квартал.

    Именно это защищает от survivorship bias: список включает бумаги, которые
    торговались в 2016-м и исчезли в 2021-м. Сегодняшний список акций их не знает.
    """
    fn = _cache_path(f"universe_{board}_{start}_{end}.json")
    if cache and os.path.exists(fn):
        with open(fn, encoding="utf-8") as f:
            return json.load(f)
    seen: set[str] = set()
    cur = start
    while cur <= end:
        seen.update(traded_securities(cur, board))
        cur += timedelta(days=step_days)
    seen.update(traded_securities(end, board))
    out = sorted(seen)
    if cache:
        with open(fn, "w", encoding="utf-8") as f:
            json.dump(out, f, ensure_ascii=False)
    return out


# ------------------------------------------------------------------ котировки

_HIST_COLS = ["TRADEDATE", "OPEN", "HIGH", "LOW", "CLOSE", "VOLUME", "VALUE"]


def history(secid: str, start: date, end: date, board: str = "TQBR",
            cache: bool = True) -> pd.DataFrame:
    """Дневные свечи. Индекс — дата, колонки open/high/low/close/volume/value."""
    fn = _cache_path(f"hist_{board}_{secid}_{start}_{end}.csv")
    if cache and os.path.exists(fn):
        return pd.read_csv(fn, index_col=0, parse_dates=True)
    df = _paged(f"{SHARES}/{board}/securities/{secid}.json", "history",
                {"from": start.isoformat(), "till": end.isoformat()})
    if df.empty:
        out = pd.DataFrame(columns=[c.lower() for c in _HIST_COLS[1:]])
    else:
        for c in _HIST_COLS:
            if c not in df:
                df[c] = pd.NA
        out = df[_HIST_COLS].copy()
        out["TRADEDATE"] = pd.to_datetime(out["TRADEDATE"])
        out = out.set_index("TRADEDATE").sort_index()
        out.columns = [c.lower() for c in out.columns]
        out = out.apply(pd.to_numeric, errors="coerce").dropna(subset=["close"])
    if cache:
        out.to_csv(fn)
    return out


def index_history(secid: str, start: date, end: date, cache: bool = True) -> pd.DataFrame:
    """История индекса: IMOEX (ценовой) или MCFTR (полной доходности)."""
    fn = _cache_path(f"idx_{secid}_{start}_{end}.csv")
    if cache and os.path.exists(fn):
        return pd.read_csv(fn, index_col=0, parse_dates=True)
    df = _paged(f"{INDEX}/{secid}.json", "history",
                {"from": start.isoformat(), "till": end.isoformat()})
    cols = [c for c in ["TRADEDATE", "OPEN", "HIGH", "LOW", "CLOSE"] if c in df.columns]
    out = df[cols].copy() if not df.empty else pd.DataFrame(columns=cols)
    if not out.empty:
        out["TRADEDATE"] = pd.to_datetime(out["TRADEDATE"])
        out = out.set_index("TRADEDATE").sort_index()
        out.columns = [c.lower() for c in out.columns]
        out = out.apply(pd.to_numeric, errors="coerce").dropna(subset=["close"])
    if cache:
        out.to_csv(fn)
    return out


# ----------------------------------------------------------------- дивиденды

def dividends(secid: str, cache: bool = True) -> pd.DataFrame:
    """Дивиденды по бумаге. Колонки: ex_date (дата закрытия реестра), value."""
    fn = _cache_path(f"div_{secid}.csv")
    if cache and os.path.exists(fn):
        return pd.read_csv(fn, parse_dates=["ex_date"])
    df = _block(_get(f"{ISS}/securities/{secid}/dividends.json"), "dividends")
    if df.empty or "registryclosedate" not in df:
        out = pd.DataFrame(columns=["ex_date", "value"])
    else:
        out = pd.DataFrame({
            "ex_date": pd.to_datetime(df["registryclosedate"], errors="coerce"),
            "value": pd.to_numeric(df.get("value"), errors="coerce"),
            "currency": df.get("currencyid", "SUR"),
        })
        out = out[(out["currency"] == "SUR") | out["currency"].isna()]
        out = out.dropna(subset=["ex_date", "value"]).sort_values("ex_date")
        out = out[["ex_date", "value"]]
    if cache:
        out.to_csv(fn, index=False)
    return out


def adjust_for_dividends(px: pd.DataFrame, divs: pd.DataFrame) -> pd.DataFrame:
    """Обратная корректировка цен на дивиденды (back-adjust).

    Для каждой отсечки цены ДО неё умножаются на (1 - D/P), где P — закрытие
    последнего дня перед отсечкой. Итог: ряд без искусственных гэпов, на котором
    RSI и SMA считаются честно, а «падение на 12%» — это реальное падение.
    """
    if px.empty or divs.empty:
        return px.copy()
    out = px.copy()
    price_cols = [c for c in ("open", "high", "low", "close") if c in out.columns]
    for _, row in divs.sort_values("ex_date", ascending=False).iterrows():
        ex = pd.Timestamp(row["ex_date"])
        before = out.index[out.index < ex]
        if len(before) == 0:
            continue
        ref = out.loc[before[-1], "close"]
        if not ref or ref <= 0 or row["value"] <= 0 or row["value"] >= ref:
            continue
        factor = 1.0 - row["value"] / ref
        out.loc[before, price_cols] = out.loc[before, price_cols] * factor
    return out


# --------------------------------------------------------------------- лоты

def lot_sizes(secids: list[str], board: str = "TQBR", cache: bool = True) -> dict[str, int]:
    fn = _cache_path(f"lots_{board}.json")
    known: dict[str, int] = {}
    if cache and os.path.exists(fn):
        with open(fn, encoding="utf-8") as f:
            known = json.load(f)
    missing = [s for s in secids if s not in known]
    for s in missing:
        try:
            df = _block(_get(f"{ISS}/securities/{s}.json"), "description")
            row = df[df.get("name") == "LOTSIZE"] if "name" in df else pd.DataFrame()
            known[s] = int(row.iloc[0]["value"]) if not row.empty else 1
        except Exception:
            known[s] = 1
    if cache and missing:
        with open(fn, "w", encoding="utf-8") as f:
            json.dump(known, f)
    return {s: known.get(s, 1) for s in secids}
