"""NSE's own end-of-day files, published the same evening (about 18:00 IST).

Yahoo often adds an NSE daily bar only the next day (observed 3-5 Nov 2026), so the
engine takes history from Yahoo and any newer sessions from these files.
"""
from __future__ import annotations

import io
import zipfile
from datetime import datetime
from typing import Callable

import pandas as pd

BHAV_URL = "https://nsearchives.nseindia.com/content/cm/BhavCopy_NSE_CM_0_0_0_{d:%Y%m%d}_F_0000.csv.zip"
INDEX_URL = "https://nsearchives.nseindia.com/content/indices/ind_close_all_{d:%d%m%Y}.csv"
# NSE blocks the default python-requests agent
HEADERS = {"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) "
                         "Chrome/124.0 Safari/537.36", "Accept": "*/*"}
SERIES = {"EQ", "BE", "BZ"}


def _get(url: str) -> bytes | None:
    import requests
    r = requests.get(url, headers=HEADERS, timeout=30)
    if r.status_code in (403, 404):
        return None                       # not published yet, a holiday, or blocked
    r.raise_for_status()
    return r.content


def fetch_bhavcopy(day: pd.Timestamp) -> pd.DataFrame | None:
    """Columns open, high, low, close, prev_close, volume; index = NSE symbol."""
    raw = _get(BHAV_URL.format(d=day))
    if raw is None:
        return None
    with zipfile.ZipFile(io.BytesIO(raw)) as z:
        df = pd.read_csv(z.open(z.namelist()[0]))
    df.columns = df.columns.str.strip()
    df = df[df["SctySrs"].astype(str).str.strip().isin(SERIES)]
    out = pd.DataFrame({
        "open": df["OpnPric"].astype(float).to_numpy(),
        "high": df["HghPric"].astype(float).to_numpy(),
        "low": df["LwPric"].astype(float).to_numpy(),
        "close": df["ClsPric"].astype(float).to_numpy(),
        "prev_close": df["PrvsClsgPric"].astype(float).to_numpy(),
        "volume": df["TtlTradgVol"].astype(float).to_numpy(),
    }, index=df["TckrSymb"].astype(str).str.strip())
    return out[~out.index.duplicated(keep="first")]


def fetch_index_close(day: pd.Timestamp, name: str = "Nifty 50") -> dict | None:
    raw = _get(INDEX_URL.format(d=day))
    if raw is None:
        return None
    df = pd.read_csv(io.BytesIO(raw))
    df.columns = df.columns.str.strip()
    row = df[df["Index Name"].astype(str).str.strip().str.lower() == name.lower()]
    if row.empty:
        return None
    r = row.iloc[0]

    def num(col):
        return float(str(r[col]).replace(",", ""))
    close = num("Closing Index Value")
    return {"open": num("Open Index Value"), "high": num("High Index Value"),
            "low": num("Low Index Value"), "close": close, "volume": 0.0}


def candidate_days(after: pd.Timestamp, now_ist: datetime, publish_hour: int = 18,
                   limit: int = 6) -> list[pd.Timestamp]:
    """Weekdays after `after`, up to today if NSE has likely published, else yesterday."""
    last = pd.Timestamp(now_ist.date())
    if now_ist.hour < publish_hour:
        last -= pd.Timedelta(days=1)
    days = pd.bdate_range(after + pd.Timedelta(days=1), last)
    return list(days[-limit:])


def append_nse_days(idx: pd.DataFrame, prices: dict[str, pd.DataFrame], now_ist: datetime,
                    fetch_bhav: Callable = fetch_bhavcopy, fetch_index: Callable = fetch_index_close,
                    ) -> tuple[pd.DataFrame, dict[str, pd.DataFrame], list[str], list[str]]:
    """Append NSE sessions newer than Yahoo's data. Returns (idx, prices, days added, notes).

    A stock's NSE bar is only added when NSE's previous close matches Yahoo's last
    close, i.e. the two series join up. A mismatch over 1% means a split or bonus went
    ex on that day: Yahoo's history is then rescaled by the same factor NSE applied."""
    if not prices:
        return idx, prices, [], []
    # Start after the session that 90% of stocks (and the index) already have; the most
    # stale 10% are usually suspended stocks, not a data gap.
    ends = sorted(df.index[-1] for df in prices.values())
    yahoo_end = min(ends[int(len(ends) * 0.1)], idx.index[-1])
    added, notes = [], []
    prices = dict(prices)
    for d in candidate_days(yahoo_end, now_ist):
        try:
            bhav = fetch_bhav(d)
        except Exception as exc:  # noqa: BLE001
            notes.append(f"{d:%Y-%m-%d} bhavcopy error: {exc}")
            break
        if bhav is None:
            continue                       # holiday, or not published yet
        n = 0
        for sym, df in prices.items():
            if df.index[-1] >= d or sym not in bhav.index:
                continue
            b = bhav.loc[sym]
            last = float(df["close"].iloc[-1])
            if last <= 0 or b["prev_close"] <= 0:
                continue
            ratio = b["prev_close"] / last
            if abs(ratio - 1) > 0.01:
                if not 0.05 <= ratio <= 1.5:
                    notes.append(f"{sym} {d:%Y-%m-%d}: NSE prev close {b['prev_close']} vs Yahoo {last}; skipped")
                    continue
                df = df.copy()
                df[["open", "high", "low", "close"]] *= ratio
                df["volume"] /= ratio
                notes.append(f"{sym} {d:%Y-%m-%d}: history rescaled x{ratio:.4f} (corporate action)")
            row = pd.DataFrame([{k: float(b[k]) for k in ("open", "high", "low", "close", "volume")}],
                               index=pd.DatetimeIndex([d]))
            prices[sym] = pd.concat([df, row])
            n += 1
        if n:
            added.append(f"{d:%Y-%m-%d}")
        if idx.index[-1] < d:
            try:
                ix = fetch_index(d)
            except Exception as exc:  # noqa: BLE001
                ix = None
                notes.append(f"{d:%Y-%m-%d} index file error: {exc}")
            if ix:
                idx = pd.concat([idx, pd.DataFrame([ix], index=pd.DatetimeIndex([d]))])
    return idx, prices, added, notes
