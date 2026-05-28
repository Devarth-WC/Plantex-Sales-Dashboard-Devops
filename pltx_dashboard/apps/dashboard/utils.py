import json
import math

import numpy as np
import pandas as pd


class DashboardEncoder(json.JSONEncoder):
    """Handles numpy/pandas types that the default encoder chokes on."""

    def default(self, obj):
        if isinstance(obj, (np.integer,)):
            return int(obj)
        if isinstance(obj, (np.floating,)):
            return float(obj)
        if isinstance(obj, np.ndarray):
            return obj.tolist()
        if isinstance(obj, (pd.Timestamp,)):
            return str(obj)
        if hasattr(obj, "isoformat"):
            return obj.isoformat()
        return super().default(obj)


def clean_currency(x):
    """
    Cleans string representation of currency (e.g. '₹44,275.00', '$100.00') into float.
    Removes commas and currency symbols.
    """
    if isinstance(x, float) and math.isnan(x):
        return 0.0
    if isinstance(x, str):
        x = x.replace("₹", "").replace("$", "").replace(",", "").strip()
    try:
        val = float(x)
        return 0.0 if math.isnan(val) else val
    except (ValueError, TypeError):
        return 0.0


def clean_number(x):
    """
    Cleans string representation of numbers (e.g. '2,559') into integer.
    """
    if isinstance(x, float) and math.isnan(x):
        return 0
    if isinstance(x, str):
        x = x.replace(",", "").strip()
    try:
        val = float(x)  # float() handles cases like '10.0'
        return 0 if math.isnan(val) else int(val)
    except (ValueError, TypeError):
        return 0


def extract_days(value):
    """Extract number of days from a string like '15 days'."""
    try:
        return int(str(value).split()[0])
    except Exception:
        return 0
