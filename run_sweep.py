"""Test the pre-declared rule variants in research/sweep.toml on one data download.

  python run_sweep.py [period]      (default 10y) -> out/sweep.md, out/sweep.json
"""
from __future__ import annotations

import json
import sys
import tomllib

import pandas as pd

from engine.backtest import run_backtest
from engine.config import ROOT, Config, load_config
from engine.data import download_index, download_prices, drop_unfinished_day, load_universe
from engine.report import trade_stats
from engine.rules import add_indicators, market_filter, rs_percentiles

OUT = ROOT / "out"


def fmt(st: dict) -> tuple[str, str, str]:
    if not st.get("trades"):
        return "0", "–", "–"
    pf = st["profit_factor"]
    return str(st["trades"]), f"{st['avg_r']:+.2f}R", "∞" if pf is None else f"{pf:.2f}"


def main() -> int:
    period = sys.argv[1] if len(sys.argv) > 1 else "10y"
    spec = tomllib.loads((ROOT / "research" / "sweep.toml").read_text())
    split = spec["split_date"]
    base = load_config()
    uni = load_universe()
    sectors = dict(zip(uni.symbol, uni.sector))
    idx = download_index(base.market["index"], period=period)
    prices = download_prices(list(uni.symbol), period=period)
    idx, prices = drop_unfinished_day(idx, prices)
    mk = market_filter(idx, base.market["fast_sma"], base.market["slow_sma"])

    rows, results = [], {}
    rs = None
    for name, overrides in spec["variants"].items():
        raw = {**base.raw, "swing": {**base.swing, **overrides}}
        cfg = Config(raw)
        ind = {s: add_indicators(df, cfg.swing) for s, df in prices.items()}
        rs = rs if rs is not None else rs_percentiles(ind)
        r = run_backtest(cfg, ind, rs, mk, sectors)
        trades = r["_trades"]
        ins = trade_stats([t for t in trades if t["entry_date"] < split])
        oos = trade_stats([t for t in trades if t["entry_date"] >= split])
        results[name] = {"overrides": overrides, "all": r["stats"], "in_sample": ins, "out_of_sample": oos,
                         "cagr": r["cagr"], "max_drawdown": r["max_drawdown"], "years": r["years"]}
        rows.append((name, *fmt(ins), *fmt(oos), f"{r['cagr']:.1%}", f"{r['max_drawdown']:.1%}"))
        print(f"{name}: in {fmt(ins)} out {fmt(oos)} cagr {r['cagr']:.1%}")

    first = next(iter(results.values()))
    L = [f"# Swing rule variants · {first['years']} years · split at {split}", "",
         "Each variant changes one thing from v1 (the trend follower combines the exit changes). "
         "Average R and profit factor are after costs.", "",
         "| Variant | Trades before split | Avg R | Profit factor | Trades after split | Avg R | "
         "Profit factor | 9-year CAGR | Worst drawdown |",
         "| --- | --- | --- | --- | --- | --- | --- | --- | --- |"]
    L += ["| " + " | ".join(r) + " |" for r in rows]
    L += ["", f"Nifty 50 CAGR over the same period: {run_backtest_nifty(mk):.1%}.", "",
          "Decision rule, declared before the run: a variant replaces v1 only if its average R is "
          "positive both before and after the split, its profit factor after the split is at least 1.2, "
          "and it has at least 15 trades after the split. If none qualifies, the swing side stays on paper "
          "and the next step is a different setup, not more tuning of this one."]
    OUT.mkdir(exist_ok=True)
    (OUT / "sweep.md").write_text("\n".join(L) + "\n")
    (OUT / "sweep.json").write_text(json.dumps(results, indent=2) + "\n")
    print("\n".join(L))
    return 0


def run_backtest_nifty(mk: pd.DataFrame, warmup: int = 260) -> float:
    c = mk["close"].iloc[warmup:]
    years = (c.index[-1] - c.index[0]).days / 365.25
    return (c.iloc[-1] / c.iloc[0]) ** (1 / years) - 1


if __name__ == "__main__":
    sys.exit(main())
