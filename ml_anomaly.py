"""Lightweight ML anomaly support layer.

Uses Isolation Forest on historical business metrics. It is deliberately a
supporting signal rather than the sole alert decision: the transparent
seasonality-aware statistical detector remains the primary detector, while
this model adds an independent ML perspective and confidence signal.
"""
from __future__ import annotations

import numpy as np
import pandas as pd
from sklearn.ensemble import IsolationForest


def add_ml_scores(df: pd.DataFrame, min_history: int = 30, window: int = 60) -> pd.DataFrame:
    """Return df with an expanding historical Isolation Forest score.

    For each day, the model is fitted only on earlier observations, avoiding
    look-ahead leakage. Lower decision_function values are more unusual.
    Rows without enough history receive a neutral score of None.
    """
    out = df.copy()
    metrics = [c for c in out.columns if c != "Date"]
    scores = [None] * len(out)
    flags = [False] * len(out)

    for i in range(len(out)):
        start = max(0, i - window)
        history = out.iloc[start:i][metrics].apply(pd.to_numeric, errors="coerce").dropna()
        if len(history) < min_history:
            continue
        current = out.iloc[[i]][metrics].apply(pd.to_numeric, errors="coerce")
        if current.isna().any(axis=None):
            continue

        model = IsolationForest(
            n_estimators=200,
            contamination=0.05,
            random_state=42,
        )
        model.fit(history)
        score = float(model.decision_function(current)[0])
        scores[i] = round(score, 4)
        # Isolation Forest scores are centered around zero; negative values
        # indicate observations more isolated than the learned historical set.
        flags[i] = score < 0

    out["ml_anomaly_score"] = scores
    out["ml_flag"] = flags
    return out
