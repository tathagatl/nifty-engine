"""Backtest the swing rules with the same Book the daily run uses (paper fills, costs, breakers)."""
from __future__ import annotations

from collections import defaultdict

import pandas as pd

from .portfolio import Book
from .report import rs as fmt_rs
from .report import trade_stats
from .rules import scan


def run_backtest(cfg, ind: dict[str, pd.DataFrame], rs: pd.DataFrame, mk: pd.DataFrame,
                 sectors: dict[str, str], warmup: int = 260) -> dict:
    sw = cfg.swing
    book = Book(cfg)
    dates = mk.index[warmup:]
    flagged: dict[pd.Timestamp, list[str]] = defaultdict(list)
    for s, df in ind.items():
        for d in df.index[df["flag"].to_numpy()]:
            flagged[d].append(s)

    curve, invested_days = [], 0
    for d in dates:
        needed = {p["symbol"] for p in book.positions} | {s["symbol"] for s in book.pending}
        bars, smas = {}, {}
        for s in needed:
            df = ind.get(s)
            if df is not None and d in df.index:
                row = df.loc[d]
                bars[s] = {"open": row["open"], "high": row["high"], "low": row["low"], "close": row["close"]}
                smas[s] = row["sma_trail"]
        book.fill_pending(d, bars, smas)
        book.run_bars(d, bars, smas)

        open_pnl = sum((bars[p["symbol"]]["close"] - p["entry_price"]) * p["qty_open"]
                       for p in book.positions if p["symbol"] in bars)
        curve.append((d, book.equity() + open_pnl))
        invested_days += bool(book.positions)

        if mk.at[d, "market_ok"] and flagged.get(d):
            if book.breakers(d, mk.index)["entries_allowed"]:
                taken, _ = book.select(scan(ind, rs, d, sw, symbols=flagged[d]), sectors)
                book.pending.extend(taken)

    eq = pd.Series(dict(curve))
    trades = book.closed_trades()
    years = max((eq.index[-1] - eq.index[0]).days / 365.25, 1e-9)
    start_cap = cfg.capital["swing"]
    dd = (eq / eq.cummax() - 1).min()
    nifty = mk.loc[dates, "close"]
    by_year = defaultdict(float)
    for t in trades:
        by_year[t["exit_date"][:4]] += t["net"]
    return {
        "start": eq.index[0].strftime("%Y-%m-%d"),
        "end": eq.index[-1].strftime("%Y-%m-%d"),
        "years": round(years, 2),
        "universe": len(ind),
        "stats": trade_stats(trades),
        "trades_per_year": round(len(trades) / years, 1),
        "final_equity": round(float(eq.iloc[-1]), 2),
        "cagr": round((eq.iloc[-1] / start_cap) ** (1 / years) - 1, 4),
        "max_drawdown": round(float(dd), 4),
        "time_invested": round(invested_days / len(dates), 3),
        "nifty_cagr": round((nifty.iloc[-1] / nifty.iloc[0]) ** (1 / years) - 1, 4),
        "by_year": {k: round(v, 2) for k, v in sorted(by_year.items())},
        "skipped_entries": len(book.notes),
        "_trades": trades,
        "_journal": book.journal,
        "_curve": eq,
    }


def backtest_markdown(r: dict, cfg) -> str:
    st = r["stats"]
    L = [f"# Swing rules backtest · {r['start']} to {r['end']} ({r['years']} years)", ""]
    if not st.get("trades"):
        return "\n".join(L + ["No trades were taken in this period."]) + "\n"
    verdict = "positive" if st["expectancy_rs"] > 0 else "NOT positive"
    L.append(f"Expectancy after costs is **{verdict}**: {fmt_rs(st['expectancy_rs'])} per trade "
             f"({st['avg_r']}R) over {st['trades']} trades.")
    L.append("")
    L.append("| Measure | Result |")
    L.append("| --- | --- |")
    rows = [
        ("Starting swing capital", fmt_rs(cfg.capital["swing"])),
        ("Ending swing capital", fmt_rs(r["final_equity"])),
        ("Annual return (CAGR)", f"{r['cagr']:.1%}"),
        ("Nifty 50 annual return, same period", f"{r['nifty_cagr']:.1%}"),
        ("Worst drawdown", f"{r['max_drawdown']:.1%}"),
        ("Trades per year", str(r["trades_per_year"])),
        ("Win rate", f"{st['win_rate']:.0%}"),
        ("Average win / average loss", f"{fmt_rs(st['avg_win'])} / {fmt_rs(st['avg_loss'])}"),
        ("Profit factor", str(st["profit_factor"])),
        ("Time with at least one position", f"{r['time_invested']:.0%}"),
        ("Entries skipped (gap or open below stop)", str(r["skipped_entries"])),
        ("Stocks tested", str(r["universe"])),
    ]
    L += [f"| {a} | {b} |" for a, b in rows]
    L.append("")
    L.append("| Year | Net P&L |")
    L.append("| --- | --- |")
    L += [f"| {y} | {fmt_rs(v)} |" for y, v in r["by_year"].items()]
    L.append("")
    L.append("Caveats: tested on today's Nifty 500 list, so companies that dropped out of the index are "
             "missing and older results look somewhat better than reality. Fills assume the open price; "
             "real fills on breakout mornings are often a little worse.")
    return "\n".join(L) + "\n"
