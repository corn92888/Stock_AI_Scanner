# Fixed T+10 Execution Strategy

`alpha_t10_breadth_top1_v1` is the project's first fixed, reproducible execution
policy. It converts the full-universe Alpha model into one explicit daily action:
`BUY_NEXT_OPEN`, `CASH`, or `REFRESH`.

## Locked rules

- score the point-in-time liquid Taiwan equity universe after the close;
- require the calibrated Top 3 mean confidence to exceed the training-only 70th
  percentile;
- require at least 50% of the eligible market to be up on the signal date;
- choose only the highest-ranked stock;
- allocate 8% of reference equity and add at most one position per day;
- hold at most 10 positions and at most two positions from one industry;
- enter at the next session open only when the opening gap is no more than 3%;
- exit at the T+10 close; never exit on the signal day;
- keep broker transmission disabled. The dashboard exposes a manual micro-sized
  decision and the paper account executes the same frozen policy.

## Historical audit

The audit uses 479,716 point-in-time rows from 2021-01-04 through 2025-12-31.
Every return includes 78.5 bps of fees, tax, and slippage. The model is refit on an
expanding window before each evaluated calendar year. Rules are selected from the
2022-2024 walk-forward years and then evaluated on 2025.

| Year | Role | Trades | Total return | Max drawdown proxy | Mean excess/trade |
| --- | --- | ---: | ---: | ---: | ---: |
| 2022 | Walk-forward | 41 | 13.13% | -4.73% | 3.92% |
| 2023 | Walk-forward | 5 | 2.44% | -1.60% | 3.85% |
| 2024 | Walk-forward | 34 | 9.50% | -6.77% | 3.24% |
| 2025 | Time holdout audit | 26 | 5.91% | -2.51% | 1.36% |

The evidence file is `data/models/deployable_strategy_v1.json`. The audit passes
all fixed economic, sample, and drawdown gates and therefore permits manual micro
execution. It does not permit automatic broker orders or unrestricted capital.

The 2025 period is chronologically out of sample for each expanding fit, but it was
visible to earlier project research. It is not a pristine never-observed holdout.
The drawdown measure is a fixed-horizon cohort-return proxy rather than a broker
ledger reconstructed from every intraday mark. These limitations remain visible in
the dashboard and are why the release starts at 8% per position with a forward
paper account.

## Commands

Rebuild the historical evidence:

```bash
venv/bin/python3 deployable_strategy.py \
  --audit-dataset data/alpha_universe_dataset.csv.gz \
  --evidence data/models/deployable_strategy_v1.json
```

Materialize the latest EOD decision in the scanner database:

```bash
venv/bin/python3 deployable_strategy.py --db data/stock_scanner.db
```

The daily workflow runs this after full-universe scoring. Intraday workflows only
refresh the published decision; they cannot substitute a new intraday stock for the
frozen EOD signal.
