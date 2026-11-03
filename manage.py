"""Small CLI for keeping state/ in step with your real Kite account in live mode.
Claude's morning task runs these after reading your Kite holdings; you can run them too.

  python manage.py show
  python manage.py open SYMBOL QTY PRICE [--date YYYY-MM-DD] [--stop PRICE]
  python manage.py exit SYMBOL QTY PRICE [--date YYYY-MM-DD] [--reason TEXT]
  python manage.py stop SYMBOL PRICE
  python manage.py go-live          # archive the paper record and switch config to live
"""
from __future__ import annotations

import argparse
import json
import re
import shutil
from datetime import date

from engine.config import ROOT, load_config
from engine.data import load_universe
from engine.portfolio import Book
from engine.rules import _fill, new_position

STATE = ROOT / "state"


def latest_signal(symbol: str) -> dict | None:
    for path in sorted((ROOT / "out" / "history").glob("*.json"), reverse=True):
        for s in json.loads(path.read_text()).get("signals", []):
            if s["symbol"] == symbol:
                return s
    return None


def main() -> None:
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("show")
    o = sub.add_parser("open")
    o.add_argument("symbol"); o.add_argument("qty", type=int); o.add_argument("price", type=float)
    o.add_argument("--date", default=date.today().isoformat()); o.add_argument("--stop", type=float)
    e = sub.add_parser("exit")
    e.add_argument("symbol"); e.add_argument("qty", type=int); e.add_argument("price", type=float)
    e.add_argument("--date", default=date.today().isoformat()); e.add_argument("--reason", default="manual exit")
    s = sub.add_parser("stop")
    s.add_argument("symbol"); s.add_argument("price", type=float)
    sub.add_parser("go-live")
    a = ap.parse_args()

    cfg = load_config()
    book = Book.load(cfg, STATE)
    find = {p["symbol"]: p for p in book.positions}

    if a.cmd == "show":
        print(json.dumps(book.positions, indent=2))
        return
    if a.cmd == "open":
        sym = a.symbol.upper()
        sig = latest_signal(sym) or {}
        stop = a.stop or sig.get("stop")
        if stop is None:
            raise SystemExit(f"No signal found for {sym}; pass --stop")
        try:
            uni = load_universe(refresh=False)
            sector = dict(zip(uni.symbol, uni.sector)).get(sym, "Unknown")
        except SystemExit:
            sector = "Unknown"
        sig = {"symbol": sym, "signal_date": sig.get("signal_date", a.date), "stop": stop, "qty": a.qty}
        book.positions.append(new_position(sig, a.date, a.price, sector, cfg.swing))
    elif a.cmd == "exit":
        p = find.get(a.symbol.upper())
        if p is None:
            raise SystemExit(f"{a.symbol} is not an open position")
        qty = min(a.qty, p["qty_open"])
        book._record(p, [_fill(p, a.date, qty, a.price, a.reason)])
        if a.reason.startswith("target") and p["qty_open"]:
            p["half_done"], p["stop"] = True, max(p["stop"], p["entry_price"])
        p["pending_exit"] = None if p["qty_open"] else p["pending_exit"]
        book.positions = [x for x in book.positions if x["qty_open"] > 0]
    elif a.cmd == "stop":
        find[a.symbol.upper()]["stop"] = a.price
    elif a.cmd == "go-live":
        archive = STATE / f"paper-{date.today().isoformat()}"
        archive.mkdir(parents=True, exist_ok=True)
        for f in STATE.glob("*.json"):
            shutil.move(str(f), archive / f.name)
        cfg_path = ROOT / "config.toml"
        cfg_path.write_text(re.sub(r'^mode = "paper"', 'mode = "live"', cfg_path.read_text(), flags=re.M))
        print(f"Paper record archived to {archive}; config switched to live.")
        return
    book.save(STATE)
    print(json.dumps(book.positions, indent=2))


if __name__ == "__main__":
    main()
