"""Swing book: open positions, queued entries, the journal of exits, and the circuit breakers.
The same Book drives the daily paper/live run and the backtest."""
from __future__ import annotations

import copy
import json
from pathlib import Path

import pandas as pd

from .rules import new_position, process_bar


class Book:
    def __init__(self, cfg, positions=None, pending=None, journal=None, notes=None):
        self.cfg = cfg
        self.sw = cfg.swing
        self.br = cfg.breakers
        self.positions: list[dict] = positions or []
        self.pending: list[dict] = pending or []      # signals waiting for the next open
        self.journal: list[dict] = journal or []      # exit fills
        self.notes: list[dict] = notes or []          # skipped entries and other events

    # ------------------------------------------------------------ persistence
    @classmethod
    def load(cls, cfg, state_dir: Path) -> "Book":
        def read(name, default):
            p = state_dir / name
            return json.loads(p.read_text()) if p.exists() else default
        return cls(cfg, read("positions.json", []), read("pending.json", []),
                   read("journal.json", []), read("notes.json", []))

    def save(self, state_dir: Path) -> None:
        state_dir.mkdir(parents=True, exist_ok=True)
        for name, obj in (("positions.json", self.positions), ("pending.json", self.pending),
                          ("journal.json", self.journal), ("notes.json", self.notes[-200:])):
            (state_dir / name).write_text(json.dumps(obj, indent=2) + "\n")

    # ------------------------------------------------------------ money
    def realized(self) -> float:
        return round(sum(f["net"] for f in self.journal), 2)

    def equity(self) -> float:
        return self.cfg.capital["swing"] + self.realized()

    def committed(self) -> float:
        held = sum(p["qty_open"] * p["entry_price"] for p in self.positions)
        queued = sum(s["value"] for s in self.pending)
        return held + queued

    def closed_trades(self) -> list[dict]:
        """One row per fully closed trade, ordered by final exit date."""
        trades: dict[tuple, dict] = {}
        for f in self.journal:
            key = (f["symbol"], f["entry_date"])
            t = trades.setdefault(key, {"symbol": f["symbol"], "entry_date": f["entry_date"],
                                        "net": 0.0, "exit_date": f["date"], "r_money": f.get("r_money"),
                                        "closed": False})
            t["net"] += f["net"]
            t["exit_date"] = max(t["exit_date"], f["date"])
            t["closed"] = t["closed"] or f.get("closes_trade", False)
        rows = [t for t in trades.values() if t["closed"]]
        rows.sort(key=lambda t: t["exit_date"])
        return rows

    # ------------------------------------------------------------ breakers
    def breakers(self, date: pd.Timestamp, trading_dates: pd.DatetimeIndex) -> dict:
        reasons = []
        ds = date.strftime("%Y-%m")
        month_pnl = sum(f["net"] for f in self.journal if f["date"].startswith(ds))
        if month_pnl <= -self.br["monthly_loss"]:
            reasons.append(f"monthly loss limit hit (Rs {month_pnl:,.0f} this month)")
        if self.equity() <= self.br["swing_floor"]:
            reasons.append(f"swing bucket at Rs {self.equity():,.0f}, at or below the "
                           f"Rs {self.br['swing_floor']:,} floor: swing trading halted, review the rules")
        trades = self.closed_trades()
        n = self.br["losing_streak"]
        if len(trades) >= n and all(t["net"] < 0 for t in trades[-n:]):
            last_exit = pd.Timestamp(trades[-1]["exit_date"])
            days_since = int(((trading_dates > last_exit) & (trading_dates <= date)).sum())
            if days_since < self.br["pause_days"]:
                reasons.append(f"{n} losses in a row: paused for {self.br['pause_days'] - days_since} "
                               f"more trading day(s)")
        return {"entries_allowed": not reasons, "reasons": reasons,
                "month_pnl": round(month_pnl, 2), "equity": round(self.equity(), 2)}

    # ------------------------------------------------------------ selection
    def select(self, candidates: list[dict], sectors: dict[str, str]) -> tuple[list[dict], list[dict]]:
        """Pick signals that fit the open-trade, sector and cash limits. Returns (taken, passed_over)."""
        taken, passed = [], []
        held = {p["symbol"] for p in self.positions} | {s["symbol"] for s in self.pending}
        sector_count: dict[str, int] = {}
        for p in self.positions + self.pending:
            sec = p.get("sector") or sectors.get(p["symbol"], "Unknown")
            sector_count[sec] = sector_count.get(sec, 0) + 1
        slots = self.sw["max_open_trades"] - len(self.positions) - len(self.pending)
        cash = self.equity() - self.committed()
        for sig in candidates:
            sec = sectors.get(sig["symbol"], "Unknown")
            why = None
            if sig["symbol"] in held:
                why = "already held or queued"
            elif slots <= 0:
                why = f"{self.sw['max_open_trades']} trades already open"
            elif sector_count.get(sec, 0) >= self.sw["max_per_sector"]:
                why = f"already {self.sw['max_per_sector']} trades in {sec}"
            elif sig["value"] > cash:
                why = f"not enough swing cash (Rs {cash:,.0f} free)"
            if why:
                passed.append({**sig, "sector": sec, "why_not": why})
                continue
            taken.append({**sig, "sector": sec})
            held.add(sig["symbol"])
            sector_count[sec] = sector_count.get(sec, 0) + 1
            slots -= 1
            cash -= sig["value"]
        return taken, passed

    # ------------------------------------------------------------ daily processing
    def _record(self, pos: dict, fills: list[dict]) -> None:
        for f in fills:
            f["entry_date"] = pos["entry_date"]
            f["entry_price"] = pos["entry_price"]
            f["r_money"] = round(pos["r"] * pos["qty"], 2)
            f["closes_trade"] = pos["qty_open"] == 0
            self.journal.append(f)

    def fill_pending(self, date: pd.Timestamp, bars: dict[str, dict], smas: dict[str, float]) -> list[dict]:
        """Paper mode: fill queued signals at today's open, or skip them."""
        ds = date.strftime("%Y-%m-%d")
        opened, still = [], []
        for sig in self.pending:
            bar = bars.get(sig["symbol"])
            if bar is None:
                if ds > sig["signal_date"]:
                    still.append(sig)        # no bar today for this stock; try next day
                continue
            o = bar["open"]
            if o > sig["max_entry"]:
                self.notes.append({"date": ds, "symbol": sig["symbol"],
                                   "event": f"skipped: opened at {o:.2f}, above {sig['max_entry']:.2f}"})
                continue
            if o <= sig["stop"]:
                self.notes.append({"date": ds, "symbol": sig["symbol"],
                                   "event": f"skipped: opened at {o:.2f}, at or below the stop"})
                continue
            pos = new_position(sig, ds, o, sig.get("sector", "Unknown"), self.sw)
            fills = process_bar(pos, ds, bar, smas.get(sig["symbol"], float("nan")), self.sw, entry_day=True)
            self._record(pos, fills)
            if pos["qty_open"] > 0:
                self.positions.append(pos)
            opened.append(pos)
        self.pending = still
        return opened

    def run_bars(self, date: pd.Timestamp, bars: dict[str, dict], smas: dict[str, float],
                 live: bool = False) -> list[dict]:
        """Apply today's bar to every open position. In live mode the real book is
        never changed by simulated fills; only the day count and exit flags move."""
        ds = date.strftime("%Y-%m-%d")
        events, keep = [], []
        for pos in self.positions:
            bar = bars.get(pos["symbol"])
            if bar is None or ds <= pos["entry_date"]:
                keep.append(pos)
                continue
            sma = smas.get(pos["symbol"], float("nan"))
            if live:
                sim = copy.deepcopy(pos)
                sim["pending_exit"] = None
                fills = process_bar(sim, ds, bar, sma, self.sw)
                pos["bars_held"], pos["reached_1r"] = sim["bars_held"], sim["reached_1r"]
                pos["pending_exit"] = pos["pending_exit"] or (sim["pending_exit"] if not fills else None)
                for f in fills:
                    events.append({**f, "expected_in_kite": True})
                keep.append(pos)
                continue
            fills = process_bar(pos, ds, bar, sma, self.sw)
            self._record(pos, fills)
            events.extend(fills)
            if pos["qty_open"] > 0:
                keep.append(pos)
        self.positions = keep
        return events
