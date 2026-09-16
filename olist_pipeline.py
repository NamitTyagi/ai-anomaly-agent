"""Build daily e-commerce KPIs from Olist's public Brazilian e-commerce dataset.

The raw Olist CSVs are intentionally not bundled with this portfolio repo.
Run download_olist_data.py first; it downloads the public, anonymized data from
Olist's public GitHub mirror. The pipeline then produces a compact daily KPI
file consumed by anomaly_agent.py.
"""
from pathlib import Path
import pandas as pd

RAW = Path("data/raw")
OUT = Path("data/processed/olist_daily_metrics.csv")

REQUIRED = [
    "olist_orders_dataset.csv",
    "olist_order_items_dataset.csv",
    "olist_order_reviews_dataset.csv",
]


def build_metrics(raw_dir=RAW, output=OUT):
    raw_dir = Path(raw_dir)
    missing = [name for name in REQUIRED if not (raw_dir / name).exists()]
    if missing:
        raise FileNotFoundError(
            "Missing Olist files: " + ", ".join(missing) +
            ". Run `python download_olist_data.py` first."
        )

    orders = pd.read_csv(raw_dir / "olist_orders_dataset.csv", parse_dates=[
        "order_purchase_timestamp", "order_delivered_customer_date", "order_estimated_delivery_date"
    ])
    items = pd.read_csv(raw_dir / "olist_order_items_dataset.csv")
    reviews = pd.read_csv(raw_dir / "olist_order_reviews_dataset.csv")

    orders["date"] = orders["order_purchase_timestamp"].dt.normalize()
    # Revenue and freight are based on non-cancelled/non-unavailable orders.
    valid_status = ~orders["order_status"].isin(["canceled", "unavailable"])
    valid_orders = orders.loc[valid_status, ["order_id", "date", "order_status",
                                              "order_delivered_customer_date",
                                              "order_estimated_delivery_date"]].copy()

    items_agg = items.groupby("order_id", as_index=False).agg(
        revenue=("price", "sum"),
        freight_cost=("freight_value", "sum"),
    )
    valid_orders = valid_orders.merge(items_agg, on="order_id", how="left")
    valid_orders[["revenue", "freight_cost"]] = valid_orders[["revenue", "freight_cost"]].fillna(0)

    daily = valid_orders.groupby("date", as_index=False).agg(
        Revenue=("revenue", "sum"),
        Orders=("order_id", "nunique"),
        Freight_Cost=("freight_cost", "sum"),
    )
    daily["Average_Order_Value"] = daily["Revenue"] / daily["Orders"].replace(0, pd.NA)

    # Late-delivery pressure: non-negative days after the promised date.
    delivered = valid_orders[valid_orders["order_delivered_customer_date"].notna() &
                             valid_orders["order_estimated_delivery_date"].notna()].copy()
    delivered["delay_days"] = (
        delivered["order_delivered_customer_date"] - delivered["order_estimated_delivery_date"]
    ).dt.total_seconds() / 86400
    delivered["delay_days"] = delivered["delay_days"].clip(lower=0)
    delay_daily = delivered.groupby("date", as_index=False)["delay_days"].mean().rename(
        columns={"delay_days": "Late_Delivery_Days"}
    )

    reviews["review_score"] = pd.to_numeric(reviews["review_score"], errors="coerce")
    review_map = reviews[["order_id", "review_score"]].dropna().drop_duplicates("order_id")
    review_daily = valid_orders[["order_id", "date"]].merge(review_map, on="order_id", how="inner")
    review_daily = review_daily.groupby("date", as_index=False)["review_score"].mean().rename(
        columns={"review_score": "Review_Score"}
    )

    cancelled = orders.loc[orders["order_status"] == "canceled"].groupby("date").size().rename("Cancelled_Orders")

    daily = daily.merge(delay_daily, on="date", how="left")
    daily = daily.merge(review_daily, on="date", how="left")
    daily = daily.merge(cancelled, on="date", how="left")
    daily["Cancelled_Orders"] = daily["Cancelled_Orders"].fillna(0)
    daily = daily.sort_values("date").reset_index(drop=True)

    # Fill only metrics where a missing value means there were no qualifying
    # observations that day. Keep the raw date coverage intact.
    daily["Late_Delivery_Days"] = daily["Late_Delivery_Days"].fillna(0)
    daily["Review_Score"] = daily["Review_Score"].ffill().bfill()
    daily = daily.rename(columns={"date": "Date"})
    output = Path(output)
    output.parent.mkdir(parents=True, exist_ok=True)
    daily.to_csv(output, index=False)
    return daily


if __name__ == "__main__":
    df = build_metrics()
    print(f"Built {len(df)} daily KPI rows -> {OUT}")
    print(df.head().to_string(index=False))
