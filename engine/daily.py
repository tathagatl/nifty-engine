"""One daily run: apply new bars to the book, check breakers, scan for setups, build the report."""
from __future__ import annotations

import math

import pandas as pd

from .portfolio import Book
from .report import trade_stats
from .rules import scan


def _bar(df: pd.DataFrame, d: pd.Timestamp) -> dict | None:
    if d not in df.index:
        return None
    row = df.loc[d]
    return {"open": float(row["open"]), "high": float(row["high"]),
            "low": float(row["low"]), "close": float(row["close"])}


def daily_step(cfg, book: Book, ind: dict[str, pd.DataFrame], rs: pd.DataFrame, mk: pd.DataFrame,
               sectors: dict[str, str], last_bar_date: str | None) -> dict | None:
    sw = cfg.swing
    live = sw["mode"] == "live"
    dates = mk.index
    new_dates = dates[dates > pd.Timestamp(last_bar_date)] if last_bar_date else dates[-1:]
    if len(new_dates) == 0:
        return None

    opened, exits = [], []
    notes_before = len(book.notes)
    for d in new_dates:
        needed = {p["symbol"] for p in book.positions} | {s["symbol"] for s in book.pending}
        bars = {s: b for s in needed if s in ind and (b := _bar(ind[s], d))}
        smas = {s: float(ind[s].at[d, "sma_trail"]) for s in bars}
        if not live:
            opened += book.fill_pending(d, bars, smas)
        exits += book.run_bars(d, bars, smas, live=live)

    as_of = new_dates[-1]
    m = mk.loc[as_of]
    market_ok = bool(m["market_ok"])
    br = book.breakers(as_of, dates)
    candidates = scan(ind, rs, as_of, sw)

    if market_ok and br["entries_allowed"]:
        taken, passed = book.select(candidates, sectors)
    else:
        why = "market filter off" if not market_ok else "; ".join(br["reasons"])
        taken = []
        passed = [{**c, "sector": sectors.get(c["symbol"], "Unknown"), "why_not": why} for c in candidates]

    if live:
        book.pending = []          # live entries come from Kite, not from the queue
    else:
        book.pending.extend(taken)

    for p in book.positions:
        if p["symbol"] in ind and as_of in ind[p["symbol"]].index:
            p["last_close"] = round(float(ind[p["symbol"]].at[as_of, "close"]), 2)

    actions = []
    if live:
        for s in taken:
            actions.append({"type": "BUY", "symbol": s["symbol"],
                            "detail": f"{s['qty']} shares at the open if at or below ₹{s['max_entry']:.2f}; "
                                      f"then set the GTTs in the signal below"})
        for p in book.positions:
            if p["pending_exit"]:
                actions.append({"type": "SELL", "symbol": p["symbol"],
                                "detail": f"{p['qty_open']} shares at the open: {p['pending_exit']}; "
                                          f"cancel its remaining GTT"})
        for f in exits:
            if f["reason"].startswith("target"):
                actions.append({"type": "MOVE STOP", "symbol": f["symbol"],
                                "detail": "target hit: move the stop on the remaining shares to your entry price"})
            else:
                actions.append({"type": "CHECK", "symbol": f["symbol"],
                                "detail": f"price reached the {f['reason']} level: confirm the GTT executed"})

    return {
        "as_of": as_of.strftime("%Y-%m-%d"),
        "mode": sw["mode"],
        "market": {"ok": market_ok, "close": float(m["close"]),
                   "fast": float(m["fast"]) if not math.isnan(m["fast"]) else 0.0,
                   "slow": float(m["slow"]) if not math.isnan(m["slow"]) else 0.0},
        "breakers": br,
        "action_required": bool(actions),
        "actions": actions,
        "signals": taken,
        "passed_over": passed,
        "opened": opened,
        "exits": exits,
        "skipped": book.notes[notes_before:],
        "positions": book.positions,
        "stats": trade_stats(book.closed_trades()),
    }
