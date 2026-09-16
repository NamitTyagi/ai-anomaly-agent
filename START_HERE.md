# START HERE

This version uses real anonymized Olist e-commerce data instead of the old synthetic business spreadsheet.

## Windows

Open PowerShell in this folder and run:

```powershell
pip install -r requirements.txt
python run_all.py
```

Then double-click:

```text
dashboard.html
```

## Verify everything

```powershell
python test_agent.py
```

Expected:

```text
All real-data pipeline regression checks passed.
```

## What the pipeline creates

```text
data/raw/                         downloaded public Olist CSVs
data/processed/olist_daily_metrics.csv   daily KPI layer
alerts.db                         alert history
anomaly_report.txt                plain-English report
dashboard_data.json               dashboard data contract
dashboard.html                    standalone dashboard
index.html                        GitHub Pages entry point
```

The raw Olist CSVs are not committed to the repository. They are downloaded reproducibly by `download_olist_data.py` from Olist's public data repository. `run_all.py` uses existing files when available, so you do not need to download them twice.
