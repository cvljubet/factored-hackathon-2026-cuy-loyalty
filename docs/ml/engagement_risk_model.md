# Engagement-risk model

How the loyalty assistant decides which customers most need a retention or re-engagement benefit.
All numbers come from `notebooks/engagement_risk_feasibility_v1.ipynb`.

```
customer behaviour → ML low-engagement risk score → rank customers by risk
                   → loyalty retention / re-engagement strategy → chatbot explains the loyalty action
```

## 1. Business problem

A loyalty programme only works while customers keep using the bank. A customer who is drifting away is
cheaper to keep than to win back, but only if the programme notices in time. Contacting everyone is
expensive and noisy, so the loyalty team needs to know **whom to target first**.

The model predicts, for every active customer, the risk that they will barely use the bank over the next
90 days. The highest-risk customers are the ones to offer a re-engagement benefit.

## 2. Target

`low_engagement_next_90d = 1` when the customer has **fewer than 2 eligible transactions** in the 90 days
after the snapshot date T.

* **Eligible transaction:** an approved, customer-initiated Purchase, Payment, Transfer, Withdrawal or
  Deposit. Bank adjustments and declined attempts don't count.
* **Customers scored:** registered before T, with at least one eligible transaction in the year before T.
* **Inputs:** only data from before T. The model never sees the future it predicts.
* **Snapshots:** quarterly.

| Split | Snapshots | Customers scored | Low-engagement rate |
|---|---|---|---|
| Train | 2024-07-01 → 2025-04-01 (4) | 428,670 | 39.0–40.0 % |
| Validation | 2025-07-01, 2025-10-01 | 239,486 | 39.1 % |
| Test (untouched until the end) | 2026-01-01 | 126,053 | 39.4 % |

## 3. Baselines

The model is compared against rules a loyalty team could apply with no ML at all.

| Baseline | In plain language |
|---|---|
| **Majority** | Assume nobody will disengage. It flags no one. |
| **Recency rule** | The longer since a customer's last transaction, the higher the risk. |
| **Frequency rule** | The fewer transactions in the last 90 days, the higher the risk. |
| **Recency + frequency** | Average a customer's rank on both rules: a classic RFM-style loyalty score. |
| **Logistic regression** | The simplest learned model: one weight per behaviour signal. |

## 4. Why the baselines matter

* A learned model is only worth deploying if it beats the simple rules a loyalty team could apply by hand.
* Logistic regression is the simple learned benchmark. LightGBM has to beat it too to justify its extra
  complexity.

## 5. Validation comparison

Every model is fitted on train and compared on validation with the same metrics.

| Model | ROC-AUC | PR-AUC | Precision | Recall | F1 | Recall@top10% | Recall@top20% | Precision@top20% | Lift@top20% |
|---|---|---|---|---|---|---|---|---|---|
| Majority | 0.500 | 0.391 | 0.000 | 0.000 | 0.000 | 0.101 | 0.200 | 0.391 | 1.00 |
| Recency rule | 0.629 | 0.530 | 0.407 | 0.916 | 0.563 | 0.166 | 0.300 | 0.586 | 1.50 |
| Frequency rule | 0.685 | 0.530 | 0.501 | 0.767 | 0.607 | 0.156 | 0.301 | 0.589 | 1.51 |
| Recency + frequency | 0.673 | 0.552 | 0.459 | 0.850 | 0.596 | 0.166 | 0.301 | 0.589 | 1.51 |
| Logistic regression (transactions only) | 0.729 | 0.594 | 0.524 | 0.798 | 0.633 | 0.172 | 0.333 | 0.651 | 1.66 |
| Logistic regression (all features) | 0.762 | 0.640 | 0.555 | 0.792 | 0.652 | 0.187 | 0.357 | 0.699 | 1.79 |
| **LightGBM** | **0.766** | **0.648** | **0.560** | 0.788 | **0.654** | **0.189** | **0.364** | **0.711** | **1.82** |

* **Thresholds:** precision, recall and F1 use each model's best threshold on validation.
* **Model selection:** LightGBM's settings were chosen on validation PR-AUC from 12 seeded configurations.
  All 12 scored within 0.647–0.648.

## 6. Test-set comparison

The 2026-01-01 snapshot was used once, after every choice was frozen.

| Model | ROC-AUC | PR-AUC | Precision | Recall | F1 | Recall@top20% | Precision@top20% | Lift@top20% |
|---|---|---|---|---|---|---|---|---|
| Recency + frequency (best rule) | 0.672 | 0.555 | 0.463 | 0.849 | 0.599 | 0.299 | 0.590 | 1.50 |
| Logistic regression (all features) | 0.765 | 0.650 | 0.566 | 0.776 | 0.654 | 0.363 | 0.715 | 1.82 |
| **LightGBM** | **0.769** | **0.656** | **0.573** | 0.769 | **0.657** | **0.367** | **0.723** | **1.83** |

**LightGBM gains** (paired bootstrap on test, 95 % CI):

* **vs the best rule:** +0.101 PR-AUC (0.097–0.105) and +6.8 points of Recall@top20%.
* **vs logistic regression:** +0.006 PR-AUC (0.004–0.008) and +0.4 points of Recall@top20%.

## 7. Business metric

> **Recall@top20%:** if the loyalty team can target only the 20 % of customers with the highest predicted
> risk, what share of all truly low-engagement customers does it reach?

On the test snapshot, about 25,000 customers are targeted out of 126,053:

| Targeting | Reaches | Share of the ~49,650 low-engagement customers |
|---|---|---|
| Random 20 % | about 9,930 | 20 % |
| Best loyalty rule | about 14,870 | 29.9 % |
| **LightGBM** | **about 18,220** | **36.7 %** |

> **Precision@top20% / Lift@top20%:** of the customers we target, how many really needed it?

With LightGBM, 72 % of the targeted customers really are heading to low engagement, against a 39 % base rate.
That is **1.83×** better than targeting at random.

## 8. Top model features

Ranked by how much validation PR-AUC drops when the feature is scrambled. These are associations, not causes.

| Feature | What it captures | PR-AUC drop |
|---|---|---|
| `products_opened` | depth of the relationship (products opened before T) | 0.109 |
| `txn_count_180d` | how often the customer transacted in the last 6 months | 0.031 |
| `n_txn_types_180d` | how many kinds of transactions they use | 0.009 |
| `spend_usd_180d` | spending in the last 6 months | 0.004 |
| `declined_count_90d` | recent declined attempts | 0.002 |
| `days_since_last_txn` | recency | 0.002 |

* **Relationship depth and frequency drive the score.** Campaign, contact-centre and digital-login signals
  add almost nothing.
* **LightGBM's edge over logistic regression is small.** Non-linear effects add about +0.005 PR-AUC and
  interactions about +0.003.

## 9. Hackathon takeaway

* Customer engagement is genuinely predictable from past behaviour; spending categories are not (we
  tested both).
* The ML score reaches **36.7 %** of low-engagement customers by contacting only 20 % of the base, against
  29.9 % for the best hand-made loyalty rule (about 3,350 more customers reached).
* 72 % of the customers the model targets really need a retention action, against a 39 % base rate.
* The result holds on a future snapshot the model never saw, with the same ranking on every snapshot.
* LightGBM is the best model, but only slightly ahead of logistic regression (+0.006 PR-AUC). The big win
  is ML over rules, not LightGBM over a simple model.

## 10. Limitations

* **Predictive, not causal.** The score says who is likely to disengage, not why, and not whether an offer
  will change it. Measuring an offer's effect would need a controlled experiment.
* **Illustrative actions.** Retention actions and benefits are illustrative: the supplied campaign data has
  no production-ready offer catalogue, and campaign clicks turned out to depend on the channel, not on the
  customer.
* **Synthetic data.** The data is synthetic. The strongest feature, `products_opened`, is safe by date
  but behaves like current product holding (Spearman 0.925 with all products), and 827,610 transactions are
  dated before their product's opening. Without it, validation PR-AUC falls to 0.604 (LightGBM), still well
  above the best rule (0.552).
* **Choices made on validation.** Thresholds and model selection used validation; the test snapshot is the
  only fully independent estimate.
* **Not yet served.** A batch pipeline now trains the model and writes scores to Gold (section 11), but the
  scores are not yet published to DynamoDB or used by the chatbot.

## 11. Productionization

```
notebook (research, selection, test)  →  ml/engagement_risk/ (frozen training + batch scoring)
  →  model artifact in the artifacts bucket  →  batch scores in Gold
  →  later: compact score published to DynamoDB  →  chatbot uses it in the loyalty flow
```

**Final model.**

* **Code:** `ml/engagement_risk/` reproduces the notebook's target, population, 26 features, leakage rules and
  frozen LightGBM parameters (77 trees, seed 7, deterministic).
* **Final fit:** all 7 labelled quarterly snapshots, 2024-07-01 → 2026-01-01 (794,209 rows); the last label
  window closes on 2026-04-01, before serving.
* **Benchmark:** the test result in section 6 stays the independent benchmark; the final fit has no held-out
  score of its own.
* **Replication guard:** before saving, the pipeline refits on the 4 train snapshots and must reproduce the
  notebook's populations and validation PR-AUC (0.6478) and ROC-AUC (0.7657). If it doesn't, it stops.
* **Reproducibility:** retraining gives a byte-identical `model.txt`. Spend sums are exact, because DuckDB's
  parallel float sums made the trees drift between runs.

**Artifacts.** In `s3://cuy-loyalty-dev-962450756990-artifacts/ml/engagement-risk/v1/`:

* `model.txt` (LightGBM native format), `metadata.json`, `feature_schema.json` and `metrics.json` (the notebook's
  validation and test results, unchanged);
* `scoring/as_of_date=<date>/summary.json` for each scoring run.

**Batch scores.** Table `cuy_loyalty_dev_gold.engagement_risk_scores`
(`s3://cuy-loyalty-dev-962450756990-lake/gold/engagement_risk_scores/`), Parquet, partitioned by `as_of_date`,
one row per customer. Columns: `customer_id, model_version, model_eligible, scoring_source, risk_score, risk_tier,
reason_code`.

| Customer | `risk_score` | `risk_tier` | `reason_code` | Loyalty meaning |
|---|---|---|---|---|
| In the model population | LightGBM probability | `high` (top 20 %), `medium` (next 30 %), `low` (rest) | — | prioritised retention |
| No eligible transaction in 365 days, tenure ≥ 90 days | NULL | `high` | `no_eligible_txn_365d` | win-back |
| No eligible transaction in 365 days, tenure < 90 days | NULL | `new_customer` | `insufficient_history_new_customer` | onboarding / activation |
| Missing registration date or unusable model input | NULL | `unknown` | `insufficient_data` | generic loyalty treatment |

No probability is invented for fallback customers. `high` therefore mixes model-scored and fallback
customers; `scoring_source` tells them apart.

**First run, as of 2026-06-17** (feature cutoff: everything dated up to and including 2026-06-17):

* **Customers:** 150,000 in total; 133,719 (89.2 %) scored by LightGBM.
* **Fallbacks:** 15,804 `no_eligible_txn_365d`, 477 new customers, 0 `insufficient_data`.
* **Model tiers:** 26,744 high (score ≥ 0.589), 40,116 medium (≥ 0.332) and 66,859 low; mean score 0.359.
* **Checks:** no duplicate customers, and every fallback row has a NULL score.

**Commands.** Run them from the repo root with the team profile:

```bash
uv run --group data python -m ml.engagement_risk.train --profile cuy-loyalty          # add --dry-run to keep it local
uv run --group data python -m ml.engagement_risk.score --profile cuy-loyalty --as-of 2026-06-17
```

**Next step:** publish a compact score per customer into the DynamoDB serving table, so the chatbot can explain
the matching loyalty action. Not built yet.
