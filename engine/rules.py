"""The rulebook as pure functions: indicators, the trend template, base breakouts,
position sizing, costs, and what happens to an open position on each daily bar."""
from __future__ import annotations

import math
from typing import Iterable

import numpy as np
import pandas as pd

TICK = 0.05

# Zerodha equity-delivery charges (zerodha.com/charges, Nov 2026)
STT = 0.001            # 0.1% on buy and sell
STAMP = 0.00015        # 0.015% on buy
TXN = 0.0000307        # NSE 0.00307% each side
SEBI = 10 / 1e7        # Rs 10 per crore each side
GST = 0.18             # on transaction + SEBI charges
DP_PER_SELL = 15.34    # per stock, per day sold


def buy_costs(value: float) -> float:
    return value * (STT + STAMP + TXN + SEBI) + GST * value * (TXN + SEBI)


def sell_costs(value: float) -> float:
    return value * (STT + TXN + SEBI) + GST * value * (TXN + SEBI) + DP_PER_SELL


def tick_down(price: float) -> float:
    return math.floor(price / TICK + 1e-9) * TICK


def tick_up(price: float) -> float:
    return math.ceil(price / TICK - 1e-9) * TICK


# ---------------------------------------------------------------- indicators

def add_indicators(df: pd.DataFrame, sw: dict) -> pd.DataFrame:
    """df has columns open, high, low, close, volume indexed by date (ascending)."""
    out = df.copy()
    c, v = out["close"], out["volume"]
    out["sma_trail"] = c.rolling(sw["trail_sma"]).mean()
    out["sma50"] = c.rolling(50).mean()
    out["sma150"] = c.rolling(150).mean()
    out["sma200"] = c.rolling(200).mean()
    out["sma200_prev"] = out["sma200"].shift(sw["sma200_rising_days"])
    out["vol50"] = v.shift(1).rolling(50).mean()          # prior 50 days, excludes today
    out["hi52"] = out["high"].rolling(252, min_periods=200).max()
    out["lo52"] = out["low"].rolling(252, min_periods=200).min()
    out["ret126"] = c / c.shift(126) - 1
    out["adv50"] = (c * v).rolling(50).mean()
    prior_high = out["high"].shift(1).rolling(sw["min_base_days"]).max()

    trend = (
        (c > out["sma50"])
        & (out["sma50"] > out["sma150"])
        & (out["sma150"] > out["sma200"])
        & (out["sma200"] > out["sma200_prev"])
        & (c >= out["hi52"] * (1 - sw["near_high_pct"]))
        & (c >= out["lo52"] * (1 + sw["above_low_pct"]))
        & (out["adv50"] >= sw["min_adv_rs"])
    )
    out["trend_ok"] = trend.fillna(False)
    out["pre_breakout"] = ((c > prior_high) & (v >= sw["volume_multiple"] * out["vol50"])).fillna(False)
    out["flag"] = out["trend_ok"] & out["pre_breakout"]
    return out


def market_filter(index_df: pd.DataFrame, fast: int, slow: int) -> pd.DataFrame:
    out = index_df.copy()
    out["fast"] = out["close"].rolling(fast).mean()
    out["slow"] = out["close"].rolling(slow).mean()
    out["market_ok"] = ((out["close"] > out["fast"]) & (out["fast"] > out["slow"])).fillna(False)
    return out


def rs_percentiles(ind: dict[str, pd.DataFrame]) -> pd.DataFrame:
    """Cross-sectional percentile of 6-month return, dates x symbols."""
    ret = pd.DataFrame({s: d["ret126"] for s, d in ind.items()})
    return ret.rank(axis=1, pct=True)


# ---------------------------------------------------------------- setups

def find_base(high: np.ndarray, low: np.ndarray, i: int, min_len: int, max_len: int,
              max_depth: float, min_low: float = -math.inf) -> tuple[float, float, int] | None:
    """Longest window ending the day before i whose high-low range stays within max_depth
    and whose low stays at or above min_low (so the stop under it is close enough to entry).
    Without min_low the window would reach back into the prior up-move and the base low
    would sit far below the breakout."""
    hh, ll, best = -math.inf, math.inf, None
    for length in range(1, max_len + 1):
        j = i - length
        if j < 0:
            break
        hh = max(hh, high[j])
        ll = min(ll, low[j])
        if (hh - ll) / hh > max_depth or ll < min_low:
            break
        if length >= min_len:
            best = (hh, ll, length)
    return best


def size_position(entry: float, stop: float, risk: float, max_value: float) -> int:
    per_share = entry - stop
    if per_share <= 0:
        return 0
    return max(0, min(math.floor(risk / per_share), math.floor(max_value / entry)))


def evaluate(symbol: str, df: pd.DataFrame, i: int, rs: float, sw: dict) -> dict | None:
    """Return a buy signal for bar i, or None. Assumes df has indicators."""
    row = df.iloc[i]
    if not row["flag"] or not (rs >= sw["rs_min_pct"]):
        return None
    close = float(row["close"])
    min_low = close * (1 - sw["max_stop_pct"]) / (1 - sw["stop_buffer"])
    base = find_base(df["high"].to_numpy(), df["low"].to_numpy(), i,
                     sw["min_base_days"], sw["max_base_days"], sw["max_base_depth"], min_low)
    if base is None:
        return None
    base_high, base_low, base_days = base
    if close <= base_high:
        return None
    stop = tick_down(base_low * (1 - sw["stop_buffer"]))
    risk_ps = close - stop
    if risk_ps <= 0 or risk_ps / close > sw["max_stop_pct"]:
        return None
    qty = size_position(close, stop, sw["risk_per_trade"], sw["max_position_value"])
    if qty < 1:
        return None
    return {
        "symbol": symbol,
        "signal_date": df.index[i].strftime("%Y-%m-%d"),
        "close": round(close, 2),
        "base_high": round(float(base_high), 2),
        "base_low": round(float(base_low), 2),
        "base_days": int(base_days),
        "stop": round(stop, 2),
        "risk_per_share": round(risk_ps, 2),
        "risk_pct": round(risk_ps / close, 4),
        "qty": int(qty),
        "value": round(qty * close, 2),
        "max_loss": round(qty * risk_ps, 2),
        "target": round(tick_up(close + sw["target_r"] * risk_ps), 2),
        "max_entry": round(tick_down(close * (1 + sw["max_gap_pct"])), 2),
        "volume_ratio": round(float(row["volume"] / row["vol50"]), 2),
        "rs_pct": round(float(rs), 3),
    }


def scan(ind: dict[str, pd.DataFrame], rs: pd.DataFrame, date: pd.Timestamp, sw: dict,
         symbols: Iterable[str] | None = None) -> list[dict]:
    out = []
    for sym in (symbols if symbols is not None else ind.keys()):
        df = ind[sym]
        if date not in df.index:
            continue
        i = df.index.get_loc(date)
        if not df["flag"].iat[i]:
            continue
        r = rs.at[date, sym] if (date in rs.index and sym in rs.columns) else np.nan
        sig = evaluate(sym, df, i, r, sw)
        if sig:
            out.append(sig)
    out.sort(key=lambda s: (-s["rs_pct"], -s["volume_ratio"]))
    return out


# ---------------------------------------------------------------- positions

def new_position(sig: dict, fill_date: str, fill_price: float, sector: str, sw: dict) -> dict:
    r = fill_price - sig["stop"]
    return {
        "symbol": sig["symbol"],
        "sector": sector,
        "signal_date": sig["signal_date"],
        "entry_date": fill_date,
        "entry_price": round(fill_price, 2),
        "initial_stop": sig["stop"],
        "stop": sig["stop"],
        "qty": sig["qty"],
        "qty_open": sig["qty"],
        "r": round(r, 2),
        "target": round(tick_up(fill_price + sw["target_r"] * r), 2),
        "half_done": False,
        "reached_1r": False,
        "bars_held": 0,
        "pending_exit": None,
        "buy_costs": round(buy_costs(sig["qty"] * fill_price), 2),
        "realized": 0.0,
    }


def _fill(pos: dict, date: str, qty: int, price: float, reason: str) -> dict:
    gross = (price - pos["entry_price"]) * qty
    # buy-side costs are charged against the first exit fill of the trade
    costs = sell_costs(qty * price) + (pos["buy_costs"] if pos["qty_open"] == pos["qty"] else 0.0)
    net = gross - costs
    pos["qty_open"] -= qty
    pos["realized"] = round(pos["realized"] + net, 2)
    return {"date": date, "symbol": pos["symbol"], "qty": qty, "price": round(price, 2),
            "reason": reason, "gross": round(gross, 2), "costs": round(costs, 2), "net": round(net, 2)}


def process_bar(pos: dict, date: str, bar: dict, sma_trail: float, sw: dict,
                entry_day: bool = False) -> list[dict]:
    """Apply one daily bar to an open position, conservatively (stops before targets).
    Mutates pos; returns the exit fills that happened on this bar."""
    fills: list[dict] = []
    o, h, l, c = bar["open"], bar["high"], bar["low"], bar["close"]

    if pos["pending_exit"] and not entry_day:
        fills.append(_fill(pos, date, pos["qty_open"], o, pos["pending_exit"]))
        return fills

    pos["bars_held"] += 1

    if not entry_day and o <= pos["stop"]:
        fills.append(_fill(pos, date, pos["qty_open"], o, "stop (gap down)"))
        return fills
    if l <= pos["stop"]:
        reason = "breakeven stop" if pos["half_done"] else "stop"
        fills.append(_fill(pos, date, pos["qty_open"], pos["stop"], reason))
        return fills

    if h >= pos["entry_price"] + sw["time_stop_r"] * pos["r"]:
        pos["reached_1r"] = True

    if not pos["half_done"] and h >= pos["target"]:
        half = pos["qty_open"] // 2 or pos["qty_open"]
        price = pos["target"] if entry_day else max(o, pos["target"])
        fills.append(_fill(pos, date, half, price, "target +2R (half)"))
        pos["half_done"] = True
        pos["stop"] = max(pos["stop"], pos["entry_price"])
        if pos["qty_open"] == 0:
            return fills

    if pos["half_done"] and not math.isnan(sma_trail) and c < sma_trail:
        pos["pending_exit"] = f"close below {sw['trail_sma']}-day average"
    elif not pos["reached_1r"] and pos["bars_held"] >= sw["time_stop_days"]:
        pos["pending_exit"] = f"time stop ({sw['time_stop_days']} days without +1R)"
    return fills
