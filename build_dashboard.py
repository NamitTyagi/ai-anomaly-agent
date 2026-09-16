"""Inject the latest dashboard JSON into the standalone HTML template."""
from pathlib import Path
import json


def main():
    data = Path("dashboard_data.json").read_text(encoding="utf-8")
    template = Path("dashboard_template.html").read_text(encoding="utf-8")
    if "__DATA_JSON__" not in template:
        raise RuntimeError("dashboard_template.html is missing __DATA_JSON__ placeholder")
    parsed = json.loads(data)
    dates = parsed.get("dates", [])
    series = parsed.get("series", {})
    if not dates:
        raise RuntimeError("dashboard_data.json contains no dates")
    bad_lengths = {name: len(values) for name, values in series.items() if len(values) != len(dates)}
    if bad_lengths:
        raise RuntimeError(f"Series length mismatch: {bad_lengths}; expected {len(dates)} rows")

    html = template.replace("__DATA_JSON__", data)
    html = html.replace("__GENERATED_AT__", parsed.get("generated_at", ""))
    html = html.replace("__DAYS__", str(len(dates)))
    if "__DATA_JSON__" in html or "__DAYS__" in html:
        raise RuntimeError("Dashboard placeholders were not fully rendered")

    Path("dashboard.html").write_text(html, encoding="utf-8")
    Path("index.html").write_text(html, encoding="utf-8")
    print(f"Built dashboard.html and index.html ({len(dates)} days, {len(series)} KPIs)")


if __name__ == "__main__":
    main()
