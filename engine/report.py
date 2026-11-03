"""Stats and the markdown report that Claude reads each morning."""
from __future__ import annotations


def rs(x: float) -> str:
    sign = "-" if x < 0 else ""
    x = abs(x)
    body = f"{x:,.0f}" if x >= 100 or x == 0 else f"{x:,.2f}"
    return f"{sign}₹{body}"


def trade_stats(trades: list[dict]) -> dict:
    n = len(trades)
    if n == 0:
        return {"trades": 0}
    nets = [t["net"] for t in trades]
    rmult = [t["net"] / t["r_money"] for t in trades if t.get("r_money")]
    wins = [x for x in nets if x > 0]
    losses = [x for x in nets if x <= 0]
    return {
        "trades": n,
        "win_rate": round(len(wins) / n, 3),
        "avg_r": round(sum(rmult) / len(rmult), 2) if rmult else None,
        "expectancy_rs": round(sum(nets) / n, 2),
        "profit_factor": round(sum(wins) / -sum(losses), 2) if losses and sum(losses) < 0 else None,
        "net": round(sum(nets), 2),
        "avg_win": round(sum(wins) / len(wins), 2) if wins else 0,
        "avg_loss": round(sum(losses) / len(losses), 2) if losses else 0,
    }


def signal_block(s: dict, company: str) -> str:
    half = s["qty"] // 2
    rest = s["qty"] - half
    gtt = (f"GTT 1 (OCO) {half} sh: stop ₹{s['stop']:.2f} / target ₹{s['target']:.2f}\n"
           f"GTT 2 (single) {rest} sh: stop ₹{s['stop']:.2f}") if half else \
          f"GTT (OCO) {s['qty']} sh: stop ₹{s['stop']:.2f} / target ₹{s['target']:.2f}"
    return (
        "```text\n"
        f"SWING BUY · {company} (NSE: {s['symbol']})\n"
        f"Trigger   Closed ₹{s['close']:.2f}, above {s['base_days']}-day base high of ₹{s['base_high']:.2f}, "
        f"volume {s['volume_ratio']}x average\n"
        f"Entry     Buy at open; skip if it opens above ₹{s['max_entry']:.2f}\n"
        f"Stop      ₹{s['stop']:.2f} (below base low)     R = ₹{s['risk_per_share']:.2f}\n"
        f"Quantity  {s['qty']} shares ({rs(s['value'])})       Max loss {rs(s['max_loss'])}\n"
        f"Plan      Sell half at ₹{s['target']:.2f} (+2R), trail the rest on the 20-day average\n"
        f"{gtt}\n"
        f"Sector    {s.get('sector', '?')} · 6-month strength top {100 - s['rs_pct'] * 100:.0f}%\n"
        "```"
    )


def build_markdown(r: dict, companies: dict[str, str]) -> str:
    live = r["mode"] == "live"
    L = [f"# Swing engine · {r['as_of']} · {'LIVE' if live else 'PAPER'}", ""]
    m = r["market"]
    L.append(f"**Market filter:** {'ON' if m['ok'] else 'OFF'} (Nifty 50 {m['close']:,.0f}, "
             f"50-day {m['fast']:,.0f}, 200-day {m['slow']:,.0f})")
    b = r["breakers"]
    why = ([] if m["ok"] else ["market filter off"]) + b["reasons"]
    L.append(f"**New entries:** {'blocked (' + '; '.join(why) + ')' if why else 'allowed'}")
    L.append(f"**Swing bucket:** {rs(b['equity'])} · this month {rs(b['month_pnl'])}")
    L.append("")

    L.append("## Actions for you")
    if not r["actions"]:
        L.append("None." if live else "None. Paper mode: the engine trades on its own.")
    for a in r["actions"]:
        L.append(f"- **{a['type']}** {a['symbol']}: {a['detail']}")
    L.append("")

    L.append("## New signals" + ("" if live else " (paper: filled at tomorrow's open)"))
    if not r["signals"]:
        L.append("None today.")
    for s in r["signals"]:
        L.append(signal_block(s, companies.get(s["symbol"], s["symbol"])))
    L.append("")

    if r["opened"]:
        L.append("## Entries filled (paper)")
        for p in r["opened"]:
            L.append(f"- {p['symbol']}: {p['qty']} @ ₹{p['entry_price']:.2f}, stop ₹{p['stop']:.2f}, "
                     f"target ₹{p['target']:.2f}")
        L.append("")
    if r["exits"]:
        L.append("## Exits" + (" expected in Kite (verify)" if live else ""))
        for f in r["exits"]:
            L.append(f"- {f['date']} {f['symbol']}: {f['qty']} @ ₹{f['price']:.2f} ({f['reason']}), "
                     f"net {rs(f['net'])}")
        L.append("")
    if r["skipped"]:
        L.append("## Skipped entries")
        for n in r["skipped"]:
            L.append(f"- {n['date']} {n['symbol']}: {n['event']}")
        L.append("")

    L.append("## Open positions")
    if not r["positions"]:
        L.append("None.")
    else:
        L.append("| Stock | Qty open | Entry | Stop | Target | Last close | Days | Status |")
        L.append("| --- | --- | --- | --- | --- | --- | --- | --- |")
        for p in r["positions"]:
            status = p["pending_exit"] or ("half sold, stop at breakeven" if p["half_done"] else "running")
            L.append(f"| {p['symbol']} | {p['qty_open']} | ₹{p['entry_price']:.2f} | ₹{p['stop']:.2f} | "
                     f"₹{p['target']:.2f} | ₹{p.get('last_close', 0):.2f} | {p['bars_held']} | {status} |")
    L.append("")

    if r["passed_over"]:
        L.append("## Setups not taken")
        for s in r["passed_over"][:10]:
            L.append(f"- {s['symbol']} (₹{s['close']:.2f}): {s['why_not']}")
        L.append("")

    st = r["stats"]
    L.append("## Record so far")
    if st.get("trades"):
        L.append(f"{st['trades']} closed trades · win rate {st['win_rate']:.0%} · average "
                 f"{st['avg_r']}R · expectancy {rs(st['expectancy_rs'])} per trade · net {rs(st['net'])}")
    else:
        L.append("No closed trades yet.")
    return "\n".join(L) + "\n"
