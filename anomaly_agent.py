"""
AI Anomaly Agent
================
Reads daily business KPIs, flags statistically unusual movement using a
seasonality- and trend-aware baseline, derives business context from related
KPIs on the same day, writes a plain-English summary, records every alert
in a local history it can learn from, and optionally sends email/Slack alerts.

Run it:
    python anomaly_agent.py

Everything else is controlled by config.yaml -- no code changes needed
to point it at a different file, change sensitivity, or turn on real
alert channels.

Design notes (for interviews):
    - Naive rolling-average baselines break on data with weekday/weekend
      patterns: comparing Saturday's traffic to Friday's average flags a
      totally normal weekend dip as an "anomaly" every single week. This
      agent instead builds a baseline per metric PER WEEKDAY, from the
      last N occurrences of that same weekday. That's a deliberate,
      explainable fix for a real failure mode of naive anomaly detection.
    - Root-cause matching against a second, independent data source (the
      campaign calendar) is what turns "revenue spiked" into "revenue
      spiked, and it lines up with the Summer Flash Sale campaign" --
      the difference between detection and explanation.
    - The feedback loop (alert_store.py) means false positives aren't a
      dead end: marking the same metric/weekday combo noisy twice raises
      the bar for that specific combination automatically. This is a
      simple, transparent stand-in for what real monitoring systems
      (Datadog, Anodot) do with far more data.
"""

import argparse
import json
import smtplib
import sys
import urllib.request
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path

import pandas as pd
import yaml

import alert_store
from ml_anomaly import add_ml_scores


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------

@dataclass
class Anomaly:
    date: pd.Timestamp
    metric: str
    value: float
    baseline_mean: float
    baseline_std: float
    z_score: float
    direction: str          # "up" or "down"
    severity: str           # "moderate" or "severe"
    root_cause: str = None  # filled in later if related KPIs provide context
    alert_id: int = None    # filled in once persisted


@dataclass
class DailyReport:
    date: pd.Timestamp
    anomalies: list = field(default_factory=list)

    @property
    def has_anomaly(self):
        return len(self.anomalies) > 0


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

def load_config(path: str = "config.yaml") -> dict:
    with open(path, "r") as f:
        return yaml.safe_load(f)


# ---------------------------------------------------------------------------
# Step 1: Load & validate both data sources
# ---------------------------------------------------------------------------

def load_metrics(path: str) -> pd.DataFrame:
    path_obj = Path(path)
    if path_obj.suffix.lower() == ".csv":
        df = pd.read_csv(path_obj)
    else:
        df = pd.read_excel(path_obj)
    if "Date" not in df.columns:
        raise ValueError("Metrics file must contain a 'Date' column.")

    df["Date"] = pd.to_datetime(df["Date"])
    df = df.sort_values("Date").reset_index(drop=True)

    numeric_cols = [c for c in df.columns if c != "Date"]
    for col in numeric_cols:
        df[col] = pd.to_numeric(df[col], errors="coerce")

    df[numeric_cols] = df[numeric_cols].ffill()
    df = df.dropna(how="all", subset=numeric_cols).reset_index(drop=True)
    return df


# ---------------------------------------------------------------------------
# Step 2: Seasonality-aware anomaly detection
# ---------------------------------------------------------------------------

def detect_anomalies(
    df: pd.DataFrame,
    baseline_occurrences: int,
    min_occurrences: int,
    z_threshold: float,
    severe_multiplier: float,
    relaxed_combos: dict,
) -> list:
    """Detect unusual KPI movement using weekday seasonality + local trend.

    The detector is intentionally conservative for real e-commerce data:
    positive/skewed KPIs are scored in log space, tiny moves are ignored, and
    moderate alerts need cross-metric support. Severe single-KPI deviations can
    still alert on their own. All baselines use only earlier observations.
    """
    import numpy as np

    metrics = [c for c in df.columns if c != "Date" and not c.startswith("ml_") and not c.startswith("_")]
    df = df.copy()
    df["_weekday"] = df["Date"].dt.day_name()
    candidates = []
    min_relative_change = 0.30

    # Metrics where a raw additive scale is more interpretable than log space.
    raw_metrics = {"Review_Score", "Late_Delivery_Days"}

    for metric in metrics:
        for weekday, group in df.groupby("_weekday"):
            group = group.sort_values("Date").reset_index(drop=True)
            values = pd.to_numeric(group[metric], errors="coerce")

            for i in range(len(group)):
                history = values.iloc[max(0, i - baseline_occurrences):i].dropna()
                if len(history) < min_occurrences:
                    continue

                hist_raw = history.to_numpy(dtype=float)
                value_i = float(values.iloc[i]) if pd.notna(values.iloc[i]) else None
                if value_i is None or not np.isfinite(value_i):
                    continue

                use_log = metric not in raw_metrics and np.all(hist_raw >= 0) and value_i >= 0
                hist = np.log1p(hist_raw) if use_log else hist_raw
                current = np.log1p(value_i) if use_log else value_i

                x = np.arange(len(hist), dtype=float)
                if len(hist) >= 4 and np.ptp(hist) > 0:
                    slope, intercept = np.polyfit(x, hist, 1)
                    expected_t = float(intercept + slope * len(hist))
                    residuals = hist - (intercept + slope * x)
                else:
                    expected_t = float(np.median(hist))
                    residuals = hist - expected_t

                mad = float(np.median(np.abs(residuals - np.median(residuals))))
                scale = 1.4826 * mad
                if scale <= 1e-9 or not np.isfinite(scale):
                    scale = float(np.std(residuals, ddof=1)) if len(residuals) > 1 else 0.0
                if scale <= 1e-9 or not np.isfinite(scale):
                    continue

                z = (current - expected_t) / scale
                effective_threshold = z_threshold * relaxed_combos.get((metric, weekday), 1.0)
                expected_raw = float(np.expm1(expected_t)) if use_log else expected_t
                relative_change = abs(value_i - expected_raw) / max(abs(expected_raw), 1e-9)

                # Review scores are bounded and should use an absolute movement gate.
                if metric == "Review_Score":
                    change_ok = abs(value_i - expected_raw) >= 0.75
                else:
                    change_ok = relative_change >= min_relative_change

                if abs(z) >= effective_threshold and change_ok:
                    direction = "up" if z > 0 else "down"
                    severity = "severe" if abs(z) >= effective_threshold * severe_multiplier else "moderate"
                    candidates.append(
                        Anomaly(
                            date=group["Date"].iloc[i], metric=metric, value=value_i,
                            baseline_mean=expected_raw, baseline_std=scale, z_score=z,
                            direction=direction, severity=severity,
                        )
                    )

    # Moderate alerts are retained only when another KPI independently moves
    # unusually on the same day. This cuts isolated noise while preserving
    # severe single-metric events.
    by_date = {}
    for a in candidates:
        by_date.setdefault(a.date, []).append(a)

    final = []
    for date, day_anomalies in by_date.items():
        for a in day_anomalies:
            if a.severity == "severe":
                final.append(a)
                continue
            supported = any(
                other.metric != a.metric and other.direction == a.direction
                for other in day_anomalies
            )
            if supported:
                final.append(a)

    return final


def group_by_day(anomalies: list) -> list:
    by_date = {}
    for a in anomalies:
        by_date.setdefault(a.date, []).append(a)
    return [
        DailyReport(date=d, anomalies=sorted(v, key=lambda a: -abs(a.z_score)))
        for d, v in sorted(by_date.items())
    ]


# ---------------------------------------------------------------------------
# Step 3: Cross-metric business context
# ---------------------------------------------------------------------------

def match_root_cause(anomaly: Anomaly, report_metrics: dict) -> str:
    """Explain an anomaly using other real KPIs observed on the same day.

    This deliberately avoids inventing marketing campaigns. The context is
    derived only from the Olist business metrics generated from the public
    transaction/order/review data.
    """
    same = report_metrics
    if anomaly.metric in {"Revenue", "Orders", "Average_Order_Value"}:
        if {"Revenue", "Orders"}.issubset(same) and same.get("Revenue") == anomaly and same.get("Orders") is not None:
            return "demand movement: revenue and order volume moved together"
        if anomaly.metric == "Revenue" and "Orders" in same and same["Orders"].direction == anomaly.direction:
            return "demand movement: revenue and order volume moved together"
        if anomaly.metric == "Average_Order_Value" and "Orders" in same and same["Orders"].direction != anomaly.direction:
            return "mix effect: order volume moved in the opposite direction"
    if anomaly.metric in {"Freight_Cost", "Late_Delivery_Days"}:
        if "Late_Delivery_Days" in same and anomaly.metric != "Late_Delivery_Days":
            return "fulfillment pressure: delivery delay moved unusually"
        if "Freight_Cost" in same and anomaly.metric != "Freight_Cost":
            return "fulfillment pressure: freight cost moved unusually"
    if anomaly.metric == "Review_Score" and "Late_Delivery_Days" in same and same["Late_Delivery_Days"].direction == "up":
        return "customer experience signal: delivery delays increased alongside review movement"
    if anomaly.metric == "Cancelled_Orders" and "Orders" in same and same["Orders"].direction == "down":
        return "order-health signal: cancellations rose while order volume fell"
    return None


# ---------------------------------------------------------------------------
# Step 4: Business-friendly summarization
# ---------------------------------------------------------------------------

BAD_WHEN_UP_DEFAULT = {"Freight_Cost", "Late_Delivery_Days", "Cancelled_Orders"}


def _pct_change(a: Anomaly) -> float:
    if a.baseline_mean == 0:
        return float("inf")
    return (a.value - a.baseline_mean) / abs(a.baseline_mean) * 100


def _describe_single(a: Anomaly) -> str:
    pct = _pct_change(a)
    verb = "jumped" if a.direction == "up" else "dropped"
    tone = "sharply" if a.severity == "severe" else "noticeably"
    base = f"{a.metric.replace('_', ' ')} {verb} {tone} to {a.value:,.2f} ({pct:+.1f}% vs. its typical {a.date.strftime('%A')})"
    if a.root_cause:
        base += f" -- context: {a.root_cause}"
    return base


def summarize_day(report: DailyReport) -> str:
    if not report.has_anomaly:
        return ""

    date_str = report.date.strftime("%A, %B %d, %Y")
    descriptions = [_describe_single(a) for a in report.anomalies]

    if len(descriptions) == 1:
        lines = [f"On {date_str}, {descriptions[0]}."]
    else:
        lines = [f"On {date_str}, several metrics moved unusually: {'; '.join(descriptions)}."]

    metrics_present = {a.metric: a for a in report.anomalies}
    unexplained = [a for a in report.anomalies if not a.root_cause]

    if "Revenue" in metrics_present and "Orders" in metrics_present:
        rev, orders = metrics_present["Revenue"], metrics_present["Orders"]
        if rev.direction == orders.direction:
            lines.append("Revenue and order volume moved in the same direction, which supports a broad demand movement rather than a single-metric artifact.")

    if "Late_Delivery_Days" in metrics_present and metrics_present["Late_Delivery_Days"].direction == "up":
        lines.append("Higher late-delivery days indicate fulfillment pressure and are worth checking against carrier or seller operations.")

    if "Review_Score" in metrics_present and metrics_present["Review_Score"].direction == "down":
        lines.append("A review-score decline is a customer-experience signal; investigate delivery, product, or seller-level drivers before treating it as random noise.")

    if any(a.severity == "severe" for a in unexplained):
        lines.append("At least one of today's moves is severe AND has no known cause -- flagging for review.")

    severities = [a.severity for a in report.anomalies]
    impact = "high" if "severe" in severities else "moderate"
    lines.append(f"Overall impact assessment: {impact}.")

    return " ".join(lines)


def build_full_summary(reports: list) -> str:
    flagged = [r for r in reports if r.has_anomaly]
    if not flagged:
        return "No anomalies detected in the reporting window. All metrics tracked within normal baseline range."
    return "\n\n".join(summarize_day(r) for r in flagged)


# ---------------------------------------------------------------------------
# Step 5: Alerting (email + Slack), both optional
# ---------------------------------------------------------------------------

def build_report_body(reports: list, source_file: str) -> str:
    generated_at = datetime.now().strftime("%Y-%m-%d %H:%M")
    summary = build_full_summary(reports)
    n_anomalies = sum(len(r.anomalies) for r in reports)
    header = (
        f"AI Anomaly Agent Report\n"
        f"Source file: {source_file}\n"
        f"Generated: {generated_at}\n"
        f"Statistical anomalies detected: {n_anomalies}\n"
        f"ML support layer: independent Isolation Forest signal\n{'-' * 50}\n\n"
    )
    return header + summary


def send_email_alert(subject: str, body: str, smtp_cfg: dict) -> None:
    msg = MIMEMultipart()
    msg["From"] = smtp_cfg["user"]
    msg["To"] = ", ".join(smtp_cfg["to"])
    msg["Subject"] = subject
    msg.attach(MIMEText(body, "plain"))
    with smtplib.SMTP(smtp_cfg["host"], smtp_cfg["port"]) as server:
        server.starttls()
        server.login(smtp_cfg["user"], smtp_cfg["password"])
        server.sendmail(smtp_cfg["user"], smtp_cfg["to"], msg.as_string())


def send_slack_alert(webhook_url: str, text: str) -> None:
    payload = json.dumps({"text": text}).encode("utf-8")
    req = urllib.request.Request(webhook_url, data=payload, headers={"Content-Type": "application/json"})
    urllib.request.urlopen(req, timeout=10)


# ---------------------------------------------------------------------------
# Dashboard data export (consumed by dashboard.html)
# ---------------------------------------------------------------------------

def export_dashboard_data(df: pd.DataFrame, reports: list,
                           history: list, out_path: str) -> None:
    metrics = [c for c in df.columns if c != "Date" and not c.startswith("ml_") and not c.startswith("_")]
    anomaly_index = {}
    for r in reports:
        for a in r.anomalies:
            anomaly_index.setdefault(a.metric, []).append({
                "date": a.date.strftime("%Y-%m-%d"),
                "value": round(float(a.value), 2),
                "z": round(float(a.z_score), 2),
                "severity": a.severity,
                "direction": a.direction,
                "root_cause": a.root_cause,
            })

    data = {
        "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M"),
        "dates": df["Date"].dt.strftime("%Y-%m-%d").tolist(),
        "series": {m: df[m].tolist() for m in metrics},
        "anomalies": anomaly_index,
        "context_notes": [
            "Context is derived from other KPIs on the same day; no campaign cause is invented.",
            "Revenue + Orders together provide a demand signal.",
            "Freight + Late_Delivery_Days provide a fulfillment signal.",
            "Review_Score provides a customer-experience signal.",
        ],
        "alert_history": history,
        "ml_scores": [
            {
                "date": row["Date"].strftime("%Y-%m-%d"),
                "score": None if pd.isna(row.get("ml_anomaly_score")) else float(row["ml_anomaly_score"]),
                "flag": bool(row.get("ml_flag", False)),
            }
            for _, row in df.iterrows()
        ],
        "summary_text": build_full_summary(reports),
    }
    Path(out_path).write_text(json.dumps(data, indent=2), encoding="utf-8")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="AI Anomaly Agent for business metrics.")
    parser.add_argument("--config", default="config.yaml")
    parser.add_argument("--output", default="anomaly_report.txt")
    parser.add_argument("--dashboard-data", default="dashboard_data.json")
    parser.add_argument("--feedback", nargs=2, metavar=("ALERT_ID", "STATUS"),
                         help="Mark a past alert: STATUS is 'reviewed' or 'false_positive'. "
                              "Example: --feedback 14 false_positive")
    args = parser.parse_args()

    cfg = load_config(args.config)
    alert_store.init_db(cfg["data"]["alert_db"])

    if args.feedback:
        alert_id, status = args.feedback
        alert_store.mark_feedback(
            cfg["data"]["alert_db"], int(alert_id), status,
            false_positive_threshold=cfg["feedback"]["false_positive_threshold"],
            relax_multiplier=cfg["feedback"]["relax_multiplier"],
        )
        print(f"Alert {alert_id} marked as '{status}'.")
        return

    print(f"Reading {cfg['data']['metrics_file']} ...")
    df = load_metrics(cfg["data"]["metrics_file"])
    print(f"Loaded {len(df)} rows, {len(df.columns) - 1} business KPIs.\n")

    # Independent ML perspective: Isolation Forest is trained only on prior
    # observations, so the model never sees the current day during fitting.
    # It supports the transparent statistical detector rather than replacing it.
    df = add_ml_scores(df, min_history=30, window=60)
    ml_scored = df["ml_anomaly_score"].notna().sum()
    ml_flagged = int(df["ml_flag"].sum())
    print(f"ML support layer: scored {ml_scored} days; {ml_flagged} ML support signal(s).\n")

    relaxed_combos = alert_store.get_relaxed_combos(cfg["data"]["alert_db"])
    if relaxed_combos:
        print(f"Applying {len(relaxed_combos)} learned adjustment(s) from past feedback:")
        for (metric, weekday), mult in relaxed_combos.items():
            print(f"  - {metric} on {weekday}s: threshold raised x{mult}")
        print()

    det = cfg["detection"]
    anomalies = detect_anomalies(
        df, baseline_occurrences=det["baseline_occurrences"], min_occurrences=det["min_occurrences"],
        z_threshold=det["z_threshold"], severe_multiplier=det["severe_multiplier"],
        relaxed_combos=relaxed_combos,
    )

    reports = group_by_day(anomalies)
    for report in reports:
        metric_map = {a.metric: a for a in report.anomalies}
        for a in report.anomalies:
            a.root_cause = match_root_cause(a, metric_map)

    flagged_days = [r for r in reports if r.has_anomaly]

    for r in flagged_days:
        for a in r.anomalies:
            a.alert_id = alert_store.record_alert(cfg["data"]["alert_db"], a, root_cause=a.root_cause)

    body = build_report_body(reports, source_file=Path(cfg["data"]["metrics_file"]).name)
    Path(args.output).write_text(body, encoding="utf-8")
    print(f"Report written to {args.output}")
    print(f"Flagged {len(flagged_days)} day(s) out of {len(df)} total.\n")
    print(body)

    history = alert_store.get_alert_history(cfg["data"]["alert_db"])
    export_dashboard_data(df, reports, history, args.dashboard_data)
    print(f"\nDashboard data written to {args.dashboard_data}")

    alert_cfg = cfg["alerts"]
    if flagged_days and alert_cfg.get("send_email"):
        smtp = alert_cfg["smtp"]
        if all([smtp["host"], smtp["user"], smtp["password"], smtp["to"], smtp["to"][0]]):
            send_email_alert(f"[Anomaly Alert] {len(flagged_days)} day(s) flagged", body, smtp)
            print("Email alert sent.")
        else:
            print("[!] send_email is true but SMTP config in config.yaml is incomplete. Skipping.", file=sys.stderr)

    if flagged_days and alert_cfg.get("send_slack"):
        webhook = alert_cfg["slack"]["webhook_url"]
        if webhook:
            send_slack_alert(webhook, body)
            print("Slack alert sent.")
        else:
            print("[!] send_slack is true but webhook_url is empty. Skipping.", file=sys.stderr)

    if flagged_days and not (alert_cfg.get("send_email") or alert_cfg.get("send_slack")):
        print("\n(Email/Slack sending is off. Fill in config.yaml and flip send_email/send_slack to true to activate.)")


if __name__ == "__main__":
    main()
