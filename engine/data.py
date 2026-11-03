"""End-of-day data: the Nifty 500 list and daily prices from Yahoo Finance (free)."""
from __future__ import annotations

import io
import time
from datetime import datetime, timedelta, timezone

import pandas as pd

from .config import ROOT

UNIVERSE_FILE = ROOT / "data" / "nifty500.csv"
UNIVERSE_URLS = [
    "https://niftyindices.com/IndexConstituent/ind_nifty500list.csv",
    "https://nsearchives.nseindia.com/content/indices/ind_nifty500list.csv",
]
UA = {"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) nifty-engine/1.0"}


def load_universe(refresh: bool = True) -> pd.DataFrame:
    """Columns: symbol, company, sector. Refreshes from NSE when possible, else uses the saved copy."""
    if refresh:
        import requests
        for url in UNIVERSE_URLS:
            try:
                r = requests.get(url, headers=UA, timeout=30)
                r.raise_for_status()
                df = pd.read_csv(io.StringIO(r.text))
                if {"Symbol", "Industry"} <= set(df.columns) and len(df) > 400:
                    UNIVERSE_FILE.parent.mkdir(parents=True, exist_ok=True)
                    df.to_csv(UNIVERSE_FILE, index=False)
                    break
            except Exception as exc:  # noqa: BLE001 - fall through to the saved copy
                print(f"universe download failed from {url}: {exc}")
    if not UNIVERSE_FILE.exists():
        raise SystemExit("No Nifty 500 list available: add data/nifty500.csv to the repo.")
    df = pd.read_csv(UNIVERSE_FILE)
    if "Series" in df.columns:
        df = df[df["Series"].astype(str).str.strip() == "EQ"]
    return pd.DataFrame({
        "symbol": df["Symbol"].str.strip(),
        "company": df.get("Company Name", df["Symbol"]).astype(str).str.strip(),
        "sector": df["Industry"].astype(str).str.strip(),
    })


def _normalise(raw: pd.DataFrame) -> pd.DataFrame:
    df = raw.rename(columns=str.lower)[["open", "high", "low", "close", "volume"]]
    df = df.dropna(subset=["open", "high", "low", "close"])
    df.index = pd.to_datetime(df.index).tz_localize(None).normalize()
    return df[~df.index.duplicated(keep="last")].sort_index()


def download_prices(symbols: list[str], period: str = "2y", min_rows: int = 260,
                    batch: int = 100) -> dict[str, pd.DataFrame]:
    """Daily OHLCV adjusted for splits and bonuses, keyed by NSE symbol."""
    import yfinance as yf

    out: dict[str, pd.DataFrame] = {}
    todo = list(symbols)
    for attempt in range(3):
        missing = []
        for k in range(0, len(todo), batch):
            chunk = todo[k:k + batch]
            tickers = [f"{s}.NS" for s in chunk]
            try:
                raw = yf.download(tickers, period=period, interval="1d", group_by="ticker",
                                  auto_adjust=True, threads=True, progress=False)
            except Exception as exc:  # noqa: BLE001
                print(f"download error ({exc}); will retry")
                missing.extend(chunk)
                continue
            for s, t in zip(chunk, tickers):
                try:
                    df = _normalise(raw[t] if len(tickers) > 1 else raw)
                except Exception:  # noqa: BLE001
                    df = pd.DataFrame()
                if len(df) >= min_rows:
                    out[s] = df
                else:
                    missing.append(s)
        if not missing or attempt == 2:
            if missing:
                print(f"no usable data for {len(missing)} symbols: {', '.join(missing[:20])}...")
            break
        todo = missing
        time.sleep(5)
    return out


IST = timezone(timedelta(hours=5, minutes=30))


def drop_unfinished_day(idx: pd.DataFrame, prices: dict[str, pd.DataFrame],
                        now: datetime | None = None) -> tuple[pd.DataFrame, dict[str, pd.DataFrame]]:
    """Before 16:00 IST today's bar is still forming; drop it so a run during market
    hours can't act on a half-finished day."""
    now = now or datetime.now(IST)
    if now.astimezone(IST).hour >= 16:
        return idx, prices
    today = pd.Timestamp(now.astimezone(IST).date())
    return idx[idx.index < today], {s: df[df.index < today] for s, df in prices.items()}


def download_index(ticker: str, period: str = "2y") -> pd.DataFrame:
    import yfinance as yf
    raw = yf.download(ticker, period=period, interval="1d", auto_adjust=True, progress=False)
    if isinstance(raw.columns, pd.MultiIndex):
        raw.columns = raw.columns.get_level_values(0)
    return _normalise(raw)
