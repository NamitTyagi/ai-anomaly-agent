"""Regression checks for the real Olist-shaped pipeline and anomaly engine."""
from pathlib import Path
import shutil
import pandas as pd

from olist_pipeline import build_metrics
from anomaly_agent import load_metrics, detect_anomalies, group_by_day, match_root_cause

TMP = Path(".test_olist_raw")
OUT = Path(".test_olist_metrics.csv")
TMP.mkdir(exist_ok=True)

dates = pd.date_range("2020-01-01", periods=70, freq="D")
orders, items, reviews = [], [], []
spike_date = pd.Timestamp("2020-03-05")
late_date = pd.Timestamp("2020-02-26")

for i, d in enumerate(dates):
    n = (4 + (i % 3)) if d != spike_date else 25
    for j in range(n):
        oid = f"o{i:03d}_{j:02d}"
        purchase = d + pd.Timedelta(hours=j)
        estimated = purchase + pd.Timedelta(days=3)
        delivered = purchase + pd.Timedelta(days=9 if d == late_date else 3 + (0.25 if i % 5 == 0 else 0))
        orders.append({
            "order_id": oid, "customer_id": f"c{i:03d}_{j:02d}", "order_status": "delivered",
            "order_purchase_timestamp": purchase,
            "order_delivered_customer_date": delivered,
            "order_estimated_delivery_date": estimated,
        })
        items.append({
            "order_id": oid, "order_item_id": 1, "product_id": "p1", "seller_id": "s1",
            "shipping_limit_date": purchase,
            "price": 95.0 + (i % 5) * 3.0,
            "freight_value": 10.0 + (i % 3) * 0.5,
        })
        reviews.append({
            "review_id": f"r{i:03d}_{j:02d}", "order_id": oid, "review_score": 5,
            "review_comment_title": "", "review_comment_message": "",
            "review_creation_date": d, "review_answer_timestamp": d,
        })

pd.DataFrame(orders).to_csv(TMP / "olist_orders_dataset.csv", index=False)
pd.DataFrame(items).to_csv(TMP / "olist_order_items_dataset.csv", index=False)
pd.DataFrame(reviews).to_csv(TMP / "olist_order_reviews_dataset.csv", index=False)

built = build_metrics(TMP, OUT)
assert len(built) == 70
expected_metrics = {
    "Revenue", "Orders", "Average_Order_Value", "Freight_Cost",
    "Late_Delivery_Days", "Review_Score", "Cancelled_Orders"
}
assert expected_metrics.issubset(built.columns)

loaded = load_metrics(OUT)
anomalies = detect_anomalies(loaded, 8, 5, 2.6, 1.5, {})
assert any(a.date == spike_date and a.metric == "Revenue" for a in anomalies)
assert any(a.date == spike_date and a.metric == "Orders" for a in anomalies)
assert any(a.date == late_date and a.metric == "Late_Delivery_Days" for a in anomalies)

reports = group_by_day(anomalies)
for report in reports:
    metric_map = {a.metric: a for a in report.anomalies}
    for a in report.anomalies:
        a.root_cause = match_root_cause(a, metric_map)

assert any(a.root_cause for r in reports for a in r.anomalies)
print("All real-data pipeline regression checks passed.")
shutil.rmtree(TMP, ignore_errors=True)
if OUT.exists():
    OUT.unlink()
