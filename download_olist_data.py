"""Download the public Olist CSVs needed by this project.

Source: Olist's public work-at-olist-data GitHub repository, which mirrors the
Brazilian E-Commerce Public Dataset by Olist. The dataset is anonymized.
"""
from pathlib import Path
from urllib.request import urlopen, Request

BASE = "https://raw.githubusercontent.com/olist/work-at-olist-data/master/datasets/"
FILES = [
    "olist_orders_dataset.csv",
    "olist_order_items_dataset.csv",
    "olist_order_reviews_dataset.csv",
]
OUT = Path("data/raw")


def download():
    OUT.mkdir(parents=True, exist_ok=True)
    for name in FILES:
        dest = OUT / name
        if dest.exists() and dest.stat().st_size > 0:
            print(f"[skip] {name} already exists")
            continue
        url = BASE + name
        print(f"[download] {name}")
        req = Request(url, headers={"User-Agent": "AI-Anomaly-Agent/1.0"})
        with urlopen(req, timeout=60) as r, open(dest, "wb") as f:
            f.write(r.read())
        print(f"         saved {dest} ({dest.stat().st_size / 1024 / 1024:.1f} MB)")


if __name__ == "__main__":
    download()
