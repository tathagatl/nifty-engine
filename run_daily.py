"""Daily entry point (GitHub Actions, weekdays after the close).

Downloads end-of-day data, runs the rulebook, and writes:
  out/latest.json, out/latest.md, out/history/<date>.json, state/*.json
"""
from __future__ import annotations

import json
import sys
from datetime import datetime, timezone

from engine.config import ROOT, load_config
from engine.daily import daily_step
from engine.data import download_index, download_prices, drop_unfinished_day, load_universe
from engine.portfolio import Book
from engine.report import build_markdown
from engine.rules import add_indicators, market_filter, rs_percentiles

STATE, OUT = ROOT / "state", ROOT / "out"


def main() -> int:
    cfg = load_config()
    sw = cfg.swing
    uni = load_universe()
    sectors = dict(zip(uni.symbol, uni.sector))
    companies = dict(zip(uni.symbol, uni.company))

    idx = download_index(cfg.market["index"])
    prices = download_prices(list(uni.symbol))
    if len(prices) < 0.8 * len(uni):
        print(f"only {len(prices)}/{len(uni)} symbols downloaded; refusing to run on partial data")
        return 1

    idx, prices = drop_unfinished_day(idx, prices)
    mk = market_filter(idx, cfg.market["fast_sma"], cfg.market["slow_sma"])

    ind = {s: add_indicators(df, sw) for s, df in prices.items()}
    rs = rs_percentiles(ind)

    meta_path = STATE / "meta.json"
    meta = json.loads(meta_path.read_text()) if meta_path.exists() else {}
    book = Book.load(cfg, STATE)
    report = daily_step(cfg, book, ind, rs, mk, sectors, meta.get("last_bar_date"))

    OUT.mkdir(exist_ok=True)
    (OUT / "history").mkdir(exist_ok=True)
    if report is None:
        print(f"no new bar since {meta.get('last_bar_date')} (holiday or data delay); nothing to do")
        status = {"as_of": meta.get("last_bar_date"), "action_required": False,
                  "note": "no new trading day", "checked_at": datetime.now(timezone.utc).isoformat()}
        (OUT / "status.json").write_text(json.dumps(status, indent=2) + "\n")
        return 0

    report["generated_at"] = datetime.now(timezone.utc).isoformat()
    report["universe_size"] = len(ind)
    book.save(STATE)
    meta.update({"last_bar_date": report["as_of"], "mode": sw["mode"]})
    meta_path.write_text(json.dumps(meta, indent=2) + "\n")

    blob = json.dumps(report, indent=2, default=str) + "\n"
    (OUT / "latest.json").write_text(blob)
    (OUT / "history" / f"{report['as_of']}.json").write_text(blob)
    (OUT / "latest.md").write_text(build_markdown(report, companies))
    (OUT / "status.json").write_text(json.dumps(
        {"as_of": report["as_of"], "action_required": report["action_required"],
         "signals": len(report["signals"]), "open_positions": len(report["positions"]),
         "checked_at": report["generated_at"]}, indent=2) + "\n")
    print((OUT / "latest.md").read_text())
    return 0


if __name__ == "__main__":
    sys.exit(main())
