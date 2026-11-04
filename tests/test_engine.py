import math
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from engine.backtest import backtest_markdown, run_backtest  # noqa: E402
from engine.config import load_config  # noqa: E402
from engine.daily import daily_step  # noqa: E402
from engine.portfolio import Book  # noqa: E402
from engine.report import build_markdown  # noqa: E402
from engine.rules import (add_indicators, buy_costs, find_base, market_filter, new_position,  # noqa: E402
                          process_bar, rs_percentiles, scan, sell_costs, size_position)

CFG = load_config()
SW = CFG.swing


def frame(closes, vols=None, start="2024-01-01", spread=0.01):
    closes = np.asarray(closes, dtype=float)
    idx = pd.bdate_range(start, periods=len(closes))
    vols = np.full(len(closes), 1_000_000.0) if vols is None else np.asarray(vols, dtype=float)
    opens = np.r_[closes[0], closes[:-1]]
    return pd.DataFrame({"open": opens, "high": np.maximum(opens, closes) * (1 + spread),
                         "low": np.minimum(opens, closes) * (1 - spread), "close": closes,
                         "volume": vols}, index=idx)


def breakout_stock(n_trend=300, base_days=20):
    trend = np.linspace(100, 200, n_trend)
    base = 199 + 3 * np.sin(np.linspace(0, 6, base_days))       # 196..202 closes
    closes = np.r_[trend, base, 207.0]
    vols = np.r_[np.full(n_trend + base_days, 1_000_000.0), 3_000_000.0]
    return frame(closes, vols)


def universe():
    n = 321
    stocks = {"BRK": breakout_stock()}
    for k in range(4):
        stocks[f"FLAT{k}"] = frame(100 + np.sin(np.linspace(0, 20, n)) * 2 + k)
    return stocks


def index_up(n=321):
    return frame(np.linspace(18000, 24000, n))


# ---------------------------------------------------------------- costs and sizing

def test_round_trip_costs_match_rulebook():
    assert buy_costs(14000) + sell_costs(14000) == pytest.approx(46.49, abs=0.02)


def test_sizing_risk_and_cap():
    assert size_position(500, 465, 1000, 15000) == 28          # rulebook example
    assert size_position(1000, 960, 1000, 15000) == 15         # value cap binds
    assert size_position(100, 100, 1000, 15000) == 0


# ---------------------------------------------------------------- setups

def test_find_base_measures_depth():
    high = np.array([110, 105, 104, 106, 105, 103, 104.0])
    low = np.array([90, 99, 98, 100, 99, 98, 99.0])
    assert find_base(high, low, 7, 3, 10, 0.15) == (106, 98, 6)   # the 110/90 bar breaks the depth limit
    assert find_base(high, low, 7, 7, 10, 0.15) is None
    assert find_base(high, low, 7, 3, 10, 0.15, min_low=97) == (106, 98, 6)
    assert find_base(high, low, 7, 3, 10, 0.15, min_low=98.5) is None   # low of 98 two bars back is too far


def test_scan_finds_breakout_with_correct_levels():
    ind = {s: add_indicators(df, SW) for s, df in universe().items()}
    rs = rs_percentiles(ind)
    d = ind["BRK"].index[-1]
    sigs = scan(ind, rs, d, SW)
    assert [s["symbol"] for s in sigs] == ["BRK"]
    s = sigs[0]
    assert s["close"] == 207.0
    assert s["base_days"] >= SW["min_base_days"]
    assert s["stop"] < s["base_low"] and s["risk_pct"] <= SW["max_stop_pct"]
    assert s["max_loss"] <= SW["risk_per_trade"] and s["value"] <= SW["max_position_value"]
    assert s["max_entry"] == pytest.approx(math.floor(207 * 1.03 / 0.05) * 0.05)
    assert s["target"] >= s["close"] + 2 * s["risk_per_share"]


def test_no_signal_without_volume():
    stocks = universe()
    stocks["BRK"].iloc[-1, stocks["BRK"].columns.get_loc("volume")] = 1_000_000
    ind = {s: add_indicators(df, SW) for s, df in stocks.items()}
    assert scan(ind, rs_percentiles(ind), ind["BRK"].index[-1], SW) == []


# ---------------------------------------------------------------- position lifecycle

def pos(entry=100.0, stop=93.0, qty=10):
    sig = {"symbol": "X", "signal_date": "2024-01-01", "stop": stop, "qty": qty}
    return new_position(sig, "2024-01-02", entry, "Tech", SW)


def bar(o, h, low, c):
    return {"open": o, "high": h, "low": low, "close": c}


def test_stop_and_gap():
    p = pos()
    f = process_bar(p, "d", bar(99, 100, 92, 95), 98, SW)
    assert f[0]["price"] == 93.0 and f[0]["reason"] == "stop" and p["qty_open"] == 0
    p = pos()
    f = process_bar(p, "d", bar(90, 91, 88, 89), 98, SW)
    assert f[0]["price"] == 90 and "gap" in f[0]["reason"]


def test_target_half_then_breakeven_then_trail():
    p = pos()
    f = process_bar(p, "d1", bar(105, 115, 104, 112), 105, SW)     # target = 114
    assert f[0]["qty"] == 5 and f[0]["price"] == 114.0
    assert p["stop"] == 100.0 and p["half_done"] and p["qty_open"] == 5
    assert process_bar(p, "d2", bar(111, 112, 108, 109), 110, SW) == []
    assert p["pending_exit"].startswith("close below")
    f = process_bar(p, "d3", bar(108, 109, 107, 108), 110, SW)
    assert f[0]["qty"] == 5 and f[0]["price"] == 108 and p["qty_open"] == 0


def test_time_stop():
    p = pos()
    for k in range(SW["time_stop_days"]):
        assert process_bar(p, f"d{k}", bar(101, 103, 99, 102), 100, SW) == []
    assert p["pending_exit"].startswith("time stop")


def test_costs_charged_once_on_first_exit():
    p = pos()
    f1 = process_bar(p, "d1", bar(105, 115, 104, 112), 105, SW)[0]
    f2 = process_bar(p, "d2", bar(99, 100, 95, 96), 98, SW)[0]       # gap below breakeven stop
    assert f1["costs"] > f2["costs"]                                   # buy costs only on the first fill


# ---------------------------------------------------------------- book and breakers

def test_breakers_streak_and_month():
    book = Book(CFG)
    dates = pd.bdate_range("2024-03-01", periods=20)
    for k in range(3):
        book.journal.append({"symbol": f"L{k}", "entry_date": "2024-03-01", "date": f"2024-03-0{k + 4}",
                             "net": -900.0, "closes_trade": True, "r_money": 1000})
    st = book.breakers(dates[5], dates)          # 2024-03-08, one trading day after the last loss
    assert not st["entries_allowed"] and any("in a row" in r for r in st["reasons"])
    assert book.breakers(dates[12], dates)["entries_allowed"] is True    # pause over; month loss 2,700 < 4,000
    book.journal.append({"symbol": "L9", "entry_date": "2024-03-01", "date": "2024-03-05",
                         "net": -1500.0, "closes_trade": True, "r_money": 1000})
    st = book.breakers(dates[15], dates)
    assert any("monthly loss" in r for r in st["reasons"])


def test_select_respects_slots_sector_and_cash():
    book = Book(CFG)
    sig = {"close": 100, "value": 13000, "rs_pct": 0.9, "volume_ratio": 2}
    cands = [{**sig, "symbol": s} for s in ["A", "B", "C", "D", "E"]]
    taken, passed = book.select(cands, {"A": "Bank", "B": "Bank", "C": "Bank", "D": "IT", "E": "IT"})
    assert [t["symbol"] for t in taken] == ["A", "B", "D"]
    assert "already 2 trades in Bank" in passed[0]["why_not"]
    assert "already open" in passed[1]["why_not"]


def test_select_blocks_trade_without_cash():
    book = Book(CFG)
    sig = {"close": 100, "value": 14500, "rs_pct": 0.9, "volume_ratio": 2}
    taken, passed = book.select([{**sig, "symbol": s} for s in "ABC"], {"A": "x", "B": "y", "C": "z"})
    assert len(taken) == 2 and "not enough swing cash" in passed[0]["why_not"]


# ---------------------------------------------------------------- end to end

def test_daily_paper_flow_signal_fill_exit(tmp_path):
    stocks, idx = universe(), index_up()
    extra = pd.bdate_range(stocks["BRK"].index[-1] + pd.offsets.BDay(), periods=3)
    path = [(208, 215, 207, 214), (214, 246, 213, 244), (244, 245, 235, 236)]
    stocks["BRK"] = pd.concat([stocks["BRK"], pd.DataFrame(
        [{"open": o, "high": h, "low": low, "close": c, "volume": 2e6} for o, h, low, c in path], index=extra)])
    for s in [k for k in stocks if k != "BRK"]:
        df = stocks[s]
        stocks[s] = pd.concat([df, pd.DataFrame([df.iloc[-1].to_dict()] * 3, index=extra)])
    idx = pd.concat([idx, pd.DataFrame([idx.iloc[-1].to_dict()] * 3, index=extra)])

    ind_full = {s: add_indicators(df, SW) for s, df in stocks.items()}
    mk_full = market_filter(idx, 50, 200)
    book, last, sectors = Book(CFG), None, {"BRK": "Capital Goods"}
    reports = []
    for cut in range(len(idx) - 3, len(idx) + 1):        # day of the breakout, then 3 more days
        mk = mk_full.iloc[:cut]
        ind = {s: d.iloc[:cut] for s, d in ind_full.items()}
        r = daily_step(CFG, book, ind, rs_percentiles(ind), mk, sectors, last)
        last = r["as_of"]
        reports.append(r)
        book.save(tmp_path)
        book = Book.load(CFG, tmp_path)

    assert reports[0]["signals"][0]["symbol"] == "BRK"
    assert reports[1]["opened"][0]["entry_price"] == 208
    tgt = [f for f in reports[2]["exits"] if f["reason"].startswith("target")]
    assert tgt and tgt[0]["qty"] == reports[1]["opened"][0]["qty"] // 2
    p = reports[3]["positions"][0]
    assert p["half_done"] and p["stop"] == p["entry_price"]          # breakeven after the target
    md = build_markdown(reports[2], {"BRK": "Breakout Ltd"})
    assert "PAPER" in md and "BRK" in md


def test_live_mode_never_changes_quantities():
    cfg = load_config()
    cfg.raw["swing"] = {**cfg.swing, "mode": "live"}
    book = Book(cfg, positions=[pos()])
    d = pd.Timestamp("2024-02-01")
    ev = book.run_bars(d, {"X": bar(99, 100, 92, 95)}, {"X": 98}, live=True)
    assert ev and ev[0]["expected_in_kite"] and book.positions[0]["qty_open"] == 10


def test_backtest_runs():
    stocks, idx = universe(), index_up()
    ind = {s: add_indicators(df, SW) for s, df in stocks.items()}
    r = run_backtest(CFG, ind, rs_percentiles(ind), market_filter(idx, 50, 200), {}, warmup=250)
    assert r["years"] > 0
    assert "backtest" in backtest_markdown(r, CFG)


def test_drop_unfinished_day_only_during_market_hours():
    from datetime import datetime
    from engine.data import IST, drop_unfinished_day
    df = frame([1, 2, 3], start="2026-11-02")          # Mon 2, Tue 3, Wed 4
    df = df.iloc[:2]                                    # bars for Nov 2 and Nov 3
    idx, prices = drop_unfinished_day(df, {"A": df}, now=datetime(2026, 11, 3, 13, 40, tzinfo=IST))
    assert idx.index[-1] == pd.Timestamp("2026-11-02") and len(prices["A"]) == 1
    idx, _ = drop_unfinished_day(df, {"A": df}, now=datetime(2026, 11, 3, 17, 10, tzinfo=IST))
    assert idx.index[-1] == pd.Timestamp("2026-11-03")


def test_no_half_variant_and_disabled_time_stop():
    sw = {**SW, "take_half": False, "time_stop_days": 0}
    p = pos()
    assert process_bar(p, "d1", bar(105, 115, 104, 112), 105, sw) == []     # target reached, nothing sold
    assert p["half_done"] and p["stop"] == 100.0 and p["qty_open"] == 10
    q = pos()
    for k in range(40):
        process_bar(q, f"d{k}", bar(101, 103, 99, 102), 100, sw)
    assert q["pending_exit"] is None                                          # no time stop


def test_index_gap_filled_from_etf_moves():
    from engine.data import fill_index_gap, latest_session
    stocks = {s: frame(np.linspace(100, 110, 30), start="2026-10-01") for s in "ABCDE"}
    idx = frame(np.linspace(23000, 23440.8, 30), start="2026-10-01").iloc[:-1]       # index one bar short
    etf = frame(np.linspace(230, 260, 30), start="2026-10-01")
    session = latest_session(stocks)
    assert session == stocks["A"].index[-1] and idx.index[-1] < session
    out, filled = fill_index_gap(idx, session, etf)
    assert filled == [session.strftime("%Y-%m-%d")] and out.index[-1] == session
    base = idx.index[-1]
    expect = idx.at[base, "close"] * etf.at[session, "close"] / etf.at[base, "close"]
    assert out.at[session, "close"] == pytest.approx(expect)
    same, none = fill_index_gap(out, session, etf)                                      # already complete
    assert none == [] and len(same) == len(out)


def test_latest_session_ignores_a_few_early_stocks():
    from engine.data import latest_session
    stocks = {s: frame(np.ones(10) * 100, start="2026-10-01") for s in "ABCDEFGHIJ"}
    stocks["A"] = frame(np.ones(11) * 100, start="2026-10-01")                         # one stock a day ahead
    assert latest_session(stocks) == stocks["B"].index[-1]
