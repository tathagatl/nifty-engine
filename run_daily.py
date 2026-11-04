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
from engine.data import (download_index, download_prices, drop_unfinished_day, fill_index_gap,
                         latest_session, load_universe)
from engine.portfolio import Book
from engine.report import build_markdown
from engine.rules import add_indicators, market_filter, rs_percentiles

STATE, OUT = ROOT / "state", ROOT / "out"
INDEX_PROXY = "NIFTYBEES.NS"      # Nifty 50 ETF, used only when ^NSEI's latest bar is late


def ymd(ts) -> str | None:
    return ts.strftime("%Y-%m-%d") if ts is not None else None


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
    session = latest_session(prices)
    index_real_end = idx.index[-1]
    filled: list[str] = []
    if session is not None and index_real_end < session:
        print(f"^NSEI ends {ymd(index_real_end)} but stocks have {ymd(session)}: filling from {INDEX_PROXY}")
        try:
            proxy, _ = drop_unfinished_day(download_index(INDEX_PROXY), {})
            idx, filled = fill_index_gap(idx, session, proxy)
        except Exception as exc:  # noqa: BLE001 - the next run catches up anyway
            print(f"index fill failed: {exc}")
    data_info = {"latest_stock_date": ymd(session), "index_last_real": ymd(index_real_end),
                 "index_filled_from_etf": filled}
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
                  "note": "no new trading day", "checked_at": datetime.now(timezone.utc).isoformat(),
                  **data_info}
        (OUT / "status.json").write_text(json.dumps(status, indent=2) + "\n")
        return 0

    report["generated_at"] = datetime.now(timezone.utc).isoformat()
    report["universe_size"] = len(ind)
    report["data"] = data_info
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
         "checked_at": report["generated_at"], **data_info}, indent=2) + "\n")
    print((OUT / "latest.md").read_text())
    return 0


if __name__ == "__main__":
    sys.exit(main())
