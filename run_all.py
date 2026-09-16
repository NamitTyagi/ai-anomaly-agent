"""Run the complete local AI Anomaly Agent pipeline in one command."""
from pathlib import Path
import subprocess
import sys


def run(script: str) -> None:
    print(f"\n=== {script} ===")
    subprocess.run([sys.executable, script], check=True)


def main():
    raw = Path("data/raw")
    required = [
        raw / "olist_orders_dataset.csv",
        raw / "olist_order_items_dataset.csv",
        raw / "olist_order_reviews_dataset.csv",
    ]
    if not all(p.exists() and p.stat().st_size > 0 for p in required):
        run("download_olist_data.py")
    else:
        print("\n=== Olist data ===")
        print("Using existing downloaded Olist CSVs.")

    run("olist_pipeline.py")
    run("anomaly_agent.py")
    run("build_dashboard.py")
    run("test_agent.py")
    print("\nDONE — pipeline + regression tests passed. Open dashboard.html in Chrome.")


if __name__ == "__main__":
    main()
