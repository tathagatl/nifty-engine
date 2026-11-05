# nifty-engine

Runs **Trading Rulebook v1** (swing side) on Nifty 500 end-of-day data. It doesn't place orders: it writes signals that Claude reads each morning and turns into a phone notification when you need to act.

## How it runs

| When (IST) | What | Where |
| --- | --- | --- |
| Weekdays 19:10 and 22:10 | Download prices, apply the rules, commit `out/` and `state/` | GitHub Actions (`daily.yml`), often hours late |
| Weekdays 08:20 | If the data is behind, push `trigger/run.txt` (starts the engine at once) and wait; then read `out/latest.json`, check Kite, research candidates, notify you if action is needed | Claude scheduled task |
| Saturday | Core-portfolio screen and research notes | Claude scheduled task |
| Month end | Performance review against the rulebook | Claude scheduled task |
| On change to rules or config | Re-run the backtest, commit `out/backtest.md` | GitHub Actions (`backtest.yml`) |

Data is free. History comes from Yahoo Finance (`yfinance`, split-adjusted), but Yahoo often adds an NSE daily bar only the next day, so newer sessions come from NSE's own end-of-day files (equity bhavcopy and `ind_close_all`, out around 18:00 IST). If NSE's previous close disagrees with Yahoo's last close by more than 1%, a split or bonus went ex that day and the history is rescaled to match. A day is processed only once most stocks have a bar for it. Kite's free API has no price history, so Kite is only used through Claude to read holdings and GTTs.

## Modes

- **paper** (default): the engine fills its own signals at the next open, runs stops, targets and trailing exits on daily bars, and keeps a journal. Nothing for you to do.
- **live**: positions mirror your Kite account. Each morning Claude compares Kite holdings with `state/positions.json` and updates it with `manage.py`. Switch with `python manage.py go-live` (archives the paper record).

## Files

| Path | What it holds |
| --- | --- |
| `config.toml` | Every rule threshold. Edit, commit, and the next run uses it |
| `engine/rules.py` | Indicators, trend template, base breakout, sizing, costs, exit logic |
| `engine/portfolio.py` | Positions, journal, circuit breakers, trade selection |
| `out/latest.md` / `latest.json` | Today's report: actions, signals with GTT levels, open positions, record |
| `out/status.json` | Small heartbeat Claude checks first (`action_required`, `as_of`) |
| `out/backtest.md` | Latest backtest summary |
| `state/*.json` | Open positions, queued entries, exit journal |

## Commands

```bash
pip install -r requirements.txt
python -m pytest -q tests            # logic tests on synthetic prices
python run_daily.py                  # one daily run
python run_backtest.py 10y           # backtest
python manage.py show                # open positions
python manage.py open TCS 10 3512.5 --stop 3260
python manage.py exit TCS 5 3990 --reason "target +2R (half)"
```

## Rule details worth knowing

- **Base:** the longest run of at least 15 days whose range stays within 15% and whose low stays within the 8% stop limit of the breakout close. Otherwise the window would reach back into the prior up-move.
- **Exits** are applied conservatively on daily bars: a stop is assumed hit before a target on the same day; a gap below the stop exits at the open.
- **Trailing and time-stop exits** trigger on the close and execute at the next open.
- **Costs** use Zerodha's delivery charges (STT, stamp, exchange, SEBI, GST, DP ₹15.34 per sell day).
- **Cash:** three full-size positions can need about ₹45,000, more than the ₹40,000 bucket, so a third trade is skipped when cash is short.

Not investment advice. You decide and place every trade.
