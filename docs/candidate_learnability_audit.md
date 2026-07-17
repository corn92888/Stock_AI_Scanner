# 候選池可學習性稽核

## 目的

當歷史模型的成本後報酬為負時，繼續增加特徵無法回答問題來源。`candidate_learnability_audit.py` 將失效原因拆成四層：

1. 候選池是否在成本後仍包含足夠的正報酬機會。
2. 決策當下可知的 18 個技術、量價與市場特徵是否能在樣本外排序未來結果。
3. Q80 棄權與每日 Top 3 組合是否能捕捉候選池機會。
4. 隔日開盤、OHLC4 代理、隔日收盤與三日回檔限價的成交假設是否造成明顯落差。

## 時間隔離

- Development：只用來訓練模型與校準 Q80 門檻。
- Development 與 validation 之間隔離 10 個交易日，避免 T+10 標籤重疊。
- Validation：只用來產生診斷指標。
- 既有 holdout 完全不評估，結果固定為 `diagnostic_only`，不得開啟正式排名。

每種進場方式分別評估 T+5 與 T+10，共八個固定診斷格。模型使用和現有同儕相對排名相同的 HistGradientBoosting、同日橫斷面百分位特徵與 training-only Q80 校準。

## 指標

- `pool`：validation 全部可交易且實際成交的候選等權結果。
- `oracle`：每天事後選出成本後淨報酬最高三檔的不可交易上限，只衡量候選池是否存在機會。
- `formal_rule`：同窗正式規則入選結果。
- `model`：只使用 development 訓練後，在 validation 產生的 Q80 Top 3 結果；空手日報酬為零。
- `mean_rank_ic`：每天預測分數與實現同儕排名的 Spearman 相關，再對交易日平均。
- `top_bottom_excess_spread`：每天預測前 20% 與後 20% 的實現超額報酬差。
- `opportunity_capture_pct`：模型日均成本後報酬相對 Oracle 上限的比例。負值代表模型在候選池存在贏家時仍選到虧損組合。
- `oracle_overlap_rate_pct`：AI 入選與同日事後 Top 3 的代號重疊率。

## 診斷規則

- `candidate_opportunity_gap`：Oracle 成本後上限不為正，優先重做候選生成。
- `feature_rankability_gap`：Oracle 為正，但 Rank IC 或前後分位超額差不為正；現有特徵無法穩定辨識贏家。
- `execution_fill_gap`：排序訊號存在，但選擇情境成交率低於 50%。
- `portfolio_construction_gap`：排序訊號存在且可成交，但 Q80／Top 3 組合的淨報酬或超額報酬仍不為正。
- `historical_edge_not_promotable`：歷史 validation 診斷為正，仍只能等待全新前瞻證據。

## 自動化

`learnability_audit.yml` 可手動執行，也會在完整歷史重播成功後自動執行。報告保留 90 天 artifact，摘要寫入研究資料庫並同步至 AI 與資料儀表板。所有自動提交使用專業英文 Conventional Commit。
