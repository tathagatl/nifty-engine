# Swing rule variants · 8.95 years · split at 2023-01-01

Each variant changes one thing from v1 (the trend follower combines the exit changes). Average R and profit factor are after costs.

| Variant | Trades before split | Avg R | Profit factor | Trades after split | Avg R | Profit factor | 9-year CAGR | Worst drawdown |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| v1 baseline | 32 | -0.01R | 1.03 | 21 | -0.21R | 0.57 | -1.1% | -17.9% |
| time stop 30 days | 29 | -0.13R | 0.83 | 18 | +0.02R | 0.96 | -1.0% | -21.3% |
| no time stop | 26 | -0.13R | 0.87 | 15 | -0.20R | 0.67 | -2.0% | -24.9% |
| trail on 50-day | 32 | +0.13R | 1.40 | 23 | -0.28R | 0.45 | -0.2% | -20.0% |
| no half at +2R | 31 | -0.00R | 1.09 | 22 | -0.23R | 0.54 | -1.1% | -19.9% |
| volume 1.3x | 40 | +0.07R | 1.26 | 29 | -0.22R | 0.59 | -0.6% | -23.2% |
| stop up to 10% | 70 | -0.01R | 0.95 | 42 | +0.35R | 2.38 | 3.3% | -17.2% |
| trend follower | 26 | +0.35R | 1.64 | 17 | -0.09R | 0.90 | 2.6% | -24.3% |

Nifty 50 CAGR over the same period: 9.6%.

Decision rule, declared before the run: a variant replaces v1 only if its average R is positive both before and after the split, its profit factor after the split is at least 1.2, and it has at least 15 trades after the split. If none qualifies, the swing side stays on paper and the next step is a different setup, not more tuning of this one.
