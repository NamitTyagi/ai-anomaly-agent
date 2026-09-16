# AI Anomaly Agent

An automated e-commerce monitoring agent built around the **Brazilian E-Commerce Public Dataset by Olist**. It converts raw order, item and review data into daily business KPIs, detects unusual movement with a seasonality- and trend-aware statistical baseline, corroborates alerts with an Isolation Forest, and generates explainable business context from related KPIs.

The goal is not another static dashboard. The project answers a practical monitoring problem:

> **Can an analytics team automatically surface unusual changes in daily e-commerce performance and tell an analyst what other business signals moved with the anomaly?**

The Olist dataset contains about 100,000 anonymized orders from 2016–2018, including order status, item prices, freight and customer reviews. The public dataset is documented by Olist on Kaggle. See the source links below.

## Business problem

An e-commerce analyst should not have to manually inspect yesterday's dashboard every morning looking for strange movement.

The agent monitors KPIs such as:

- Revenue
- Orders
- Average Order Value
- Freight Cost
- Late Delivery Days
- Review Score
- Cancelled Orders

It asks:

1. Is today's value unusual **for this weekday**?
2. Is the anomaly severe enough to alert on?
3. Did related KPIs move at the same time?
4. Does the machine-learning layer independently support the signal?
5. Has this exact metric/weekday combination generated noisy alerts before?

## Architecture

```text
Olist public CSV data
        |
        v
download_olist_data.py
        |
        v
olist_pipeline.py
(raw orders/items/reviews -> daily KPIs)
        |
        v
Seasonality-aware statistical detector
        |
        +----> Isolation Forest ML support
        |
        v
Cross-metric business context
        |
        v
SQLite alert history + feedback loop
        |
        +----> dashboard_data.json -> dashboard.html
        |
        v
GitHub Actions -> refresh raw data -> rebuild KPIs -> detect -> dashboard
```

## Why the anomaly detector is interesting

A naive monitoring rule such as `value > 7-day average` can flag normal weekday/weekend behavior repeatedly. This project compares each observation with **past observations from the same weekday**, fits a local trend to that history, and refuses to make a decision until enough history exists.

The primary detector is intentionally transparent and conservative: it uses a robust residual scale plus a minimum relative-change gate to reduce alert noise on trending, sparse business data. The Isolation Forest is a second opinion, not a black-box replacement for the alert rule.

The explanation layer also avoids inventing causes. It uses observed relationships between KPIs, for example:

- Revenue + Orders moving together → broad demand movement
- Freight + Late Delivery Days moving together → fulfillment pressure
- Review Score falling while delivery delays rise → customer-experience signal
- Cancellations rising while Orders fall → order-health signal

## Data source

Olist publishes the **Brazilian E-Commerce Public Dataset** as anonymized commercial data. It contains roughly 100,000 orders and multiple related tables covering orders, items, payments, customers, reviews, products and sellers.

- Kaggle dataset: https://www.kaggle.com/datasets/olistbr/brazilian-ecommerce
- Olist public data repository used by this project: https://github.com/olist/work-at-olist-data

The project downloads only the three raw tables required for its current KPI layer: orders, order items and reviews. The raw dataset is **not committed to this repository** because it is unnecessary for the codebase and can be downloaded reproducibly.

## Run locally

### 1. Install dependencies

```bash
pip install -r requirements.txt
```

### 2. Download the public Olist data

```bash
python download_olist_data.py
```

This creates:

```text
data/
└── raw/
    ├── olist_orders_dataset.csv
    ├── olist_order_items_dataset.csv
    └── olist_order_reviews_dataset.csv
```

### 3. Build the KPI layer

```bash
python olist_pipeline.py
```

This creates:

```text
data/processed/olist_daily_metrics.csv
```

### 4. Run the anomaly agent

```bash
python anomaly_agent.py
```

### 5. Build the dashboard

```bash
python build_dashboard.py
```

Then open **`dashboard.html`** in Chrome.

### 6. Run regression tests

```bash
python test_agent.py
```

The tests use a small Olist-shaped fixture, so you can verify the transformation and anomaly logic without downloading the full dataset.

## Alert feedback loop

Alerts are stored in `alerts.db`.

```bash
python anomaly_agent.py --feedback 14 false_positive
python anomaly_agent.py --feedback 14 reviewed
```

If the same metric/weekday combination is marked false-positive enough times, the detector raises the threshold for that combination. Alert writes are idempotent, so rerunning the same dataset does not duplicate alerts.

## Dashboard

The dashboard is deliberately custom-built rather than dependent on Power BI or Tableau:

- HTML/CSS for the interface
- JavaScript for interaction
- Canvas API for the time-series chart
- JSON generated by Python as the data contract

That keeps the demo portable: the final dashboard is a single standalone HTML file.

## GitHub Actions

`.github/workflows/daily_check.yml` can run the full pipeline automatically:

```text
Download public Olist data
        ↓
Build daily KPI layer
        ↓
Run anomaly detector
        ↓
Rebuild dashboard
        ↓
Commit refreshed outputs
```

The workflow is a reproducible monitoring demo. Olist is a historical dataset, so this should be described as **scheduled reprocessing of public historical data**, not as a claim that the project receives a live production feed.

## What's real vs. deliberately simple

**Real:**

- Public anonymized commercial dataset from Olist
- Actual multi-table data transformation
- Seasonality-aware statistical detection
- Isolation Forest ML support
- SQLite alert history and feedback
- Automated reproducible pipeline
- Custom interactive dashboard

**Deliberately simple:**

- KPI layer currently uses three Olist source tables
- Cross-metric explanations are deterministic business rules, not an LLM
- SQLite is the alert store rather than a production warehouse
- GitHub Actions demonstrates scheduled orchestration rather than a live streaming system

## Possible future extensions

- Add payments and seller tables for richer KPIs
- Add category/seller-level anomaly drill-down
- Add an optional LLM explanation layer fed only with structured evidence
- Add an actual warehouse/API as the data source
- Add trend-adjusted baselines such as EWMA
- Add email/Slack delivery through GitHub Actions secrets

## Interview talking points

**Why not just use Power BI?**

> "The point was to build the monitoring logic itself. The dashboard is only the presentation layer. Python transforms raw business data, detects anomalies, corroborates them with ML, stores alert history and generates the dashboard automatically."

**Why same-weekday baselines?**

> "A generic rolling average treated normal weekend behavior as anomalous. Comparing Monday with previous Mondays made the baseline much more representative and explainable."

**Why use Isolation Forest if there is already a statistical detector?**

> "I wanted an independent ML signal without making the alert decision a black box. The statistical rule remains the primary decision and Isolation Forest acts as supporting evidence."

**What business problem does it solve?**

> "It reduces the manual effort of monitoring daily e-commerce KPIs by surfacing unusual changes and showing which related business signals moved with them, so an analyst knows where to investigate first."


### Dashboard readability
The trend chart defaults to the latest 120 observations, with 90d / 120d / 180d / All controls. The full 614-day dataset remains available to the detector and alert history.
