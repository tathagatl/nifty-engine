"""Backtest entry point: python run_backtest.py [period]   (default 10y)."""
from __future__ import annotations

import sys

import pandas as pd

from engine.backtest import backtest_markdown, run_backtest
from engine.config import ROOT, load_config
from engine.data import download_index, download_prices, drop_unfinished_day, load_universe
from engine.rules import add_indicators, market_filter, rs_percentiles

OUT = ROOT / "out"


def main() -> int:
    period = sys.argv[1] if len(sys.argv) > 1 else "10y"
    cfg = load_config()
    uni = load_universe()
    sectors = dict(zip(uni.symbol, uni.sector))
    idx = download_index(cfg.market["index"], period=period)
    prices = download_prices(list(uni.symbol), period=period)
    idx, prices = drop_unfinished_day(idx, prices)
    mk = market_filter(idx, cfg.market["fast_sma"], cfg.market["slow_sma"])
    ind = {s: add_indicators(df, cfg.swing) for s, df in prices.items()}
    rs = rs_percentiles(ind)
    r = run_backtest(cfg, ind, rs, mk, sectors)

    OUT.mkdir(exist_ok=True)
    md = backtest_markdown(r, cfg)
    (OUT / "backtest.md").write_text(md)
    pd.DataFrame(r["_journal"]).to_csv(OUT / "backtest_fills.csv", index=False)
    r["_curve"].rename("equity").to_csv(OUT / "backtest_equity.csv")
    print(md)
    return 0


if __name__ == "__main__":
    sys.exit(main())
