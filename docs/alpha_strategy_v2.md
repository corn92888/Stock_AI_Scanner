# Alpha Strategy V2

Alpha Strategy v2 replaces the legacy-rule candidate prefilter with a point-in-time
liquid-equity universe. The old trend, reversal, and consolidation rules remain only
as frozen comparison evidence.

## Dataset

`alpha_universe_dataset.py` builds one decision-close row per historically eligible
stock. Eligibility uses only information available on that date:

- official point-in-time listing membership;
- price of at least TWD 5;
- 20-session average turnover of at least TWD 100 million;
- 20-session average volume of at least 100,000 shares;
- no same-day near-limit move;
- complete next-open T+5, T+10, and T+20 labels.

Features cover medium-term momentum, trend distance, volatility, ATR, RSI, volume,
liquidity, market breadth, industry breadth, and industry-relative strength. Labels
use next-session open entry, adjusted prices, fees, tax, and slippage.

The generated dataset is too large for normal Git history. The workflow publishes it
to the `research-alpha-data-v2` GitHub release with a SHA-256 metadata file.

## Strategy challenge

`alpha_strategy_v2.py` evaluates six locked candidates:

- T+5, T+10, and T+20 benchmark-excess targets;
- T+5, T+10, and T+20 downside-penalized excess targets.

Each fold uses an expanding training window, a horizon-length embargo, a separate
training-only calibration window, and a diversified Top 3 portfolio with at most one
stock per industry. The model may hold cash when its daily confidence does not clear
the calibration threshold.

The final holdout is not used to select the winning candidate. It is opened exactly
once only after a candidate passes the pre-holdout walk-forward gates. A failed
holdout returns the strategy to `CASH`; a passed holdout permits only prospective
shadow trading, never automatic real-money execution.

The first governed run selected the T+10 benchmark-excess model. It passed six of
eight true walk-forward folds and then passed the untouched 2025 holdout. The
holdout contained 315 simulated trades across 105 active dates, with 1.80% mean
after-cost return per trade, 0.21% mean benchmark excess, and -11.82% maximum
portfolio drawdown. These historical results authorize paper trading only.

## Prospective operation

After a successful challenge, the workflow publishes a versioned joblib model
artifact. The daily EOD scan restores that artifact and scores the complete liquid
universe with the same features, anti-chase rules, industry diversification, and
calibrated confidence threshold used in research.

The model artifact records exact NumPy, pandas, scikit-learn, and joblib versions.
Live scoring fails closed when its runtime differs, preventing silent model drift.

- weak confidence creates an explicit cash day;
- active days create at most three paper signals from different industries;
- the `alpha_v2_top3_t10_v1` account enters at the next session open;
- each position exits at the T+10 close with fees, tax, and slippage applied;
- real-money recommendations remain disabled until prospective evidence is reviewed.

## Commands

```bash
python alpha_universe_dataset.py \
  --start 2021-01-01 \
  --end 2025-12-31 \
  --output data/alpha_universe_dataset.csv.gz

python alpha_strategy_v2.py \
  --dataset data/alpha_universe_dataset.csv.gz \
  --db data/stock_scanner.db \
  --model-output data/models/alpha_strategy_v2_model.joblib

python alpha_live.py \
  --model data/models/alpha_strategy_v2_model.joblib \
  --db data/stock_scanner.db
```
