"""
asin_monthly_summary.py

Pre-aggregated monthly ASIN/FSN metrics for fast dashboard queries.

Architecture:
  ProcessedDashboardData (daily, per asin) → GROUP BY month → DashboardAsinMonthlySummary
  DashboardDailySummary (daily, no asin)   → fast KPI/charts
  DashboardAsinMonthlySummary              → fast activity counts + top/declining products

For "last 3 months" this reduces DB scan from 90 rows/ASIN to 3 rows/ASIN (30× speedup).
For "last 1 year" the reduction is 365→12 rows/ASIN (30× speedup).

Only used for date ranges ≥ 45 days (last_3_months / last_6_months / last_1_year
or custom ranges). Shorter ranges use ProcessedDashboardData directly — they're already fast.
"""

import datetime

from django.db import connection
from django.db.models import Sum
from django.utils import timezone

from apps.dashboard.models import (
    DashboardAsinMonthlySummary,
    ProcessedDashboardData,
    FlipkartProcessedDashboardData,
)


# ---------------------------------------------------------------------------
# Build / Rebuild
# ---------------------------------------------------------------------------

def rebuild_asin_monthly_summary_for_user(user, *, only_months=None):
    """
    Rebuild DashboardAsinMonthlySummary for *user* using INSERT INTO … SELECT.

    only_months: optional list of date objects (first day of month) to limit
    the rebuild to specific months; if omitted, all months are rebuilt.
    """
    # Normalise month inputs once, then use sargable date ranges in SQL.
    only_month_starts = []
    if only_months:
        for m in only_months:
            try:
                year, month = [int(part) for part in str(m)[:7].split("-")]
                only_month_starts.append(datetime.date(year, month, 1))
            except Exception:
                pass
    only_month_starts = sorted(set(only_month_starts))
    only_month_strs = [month_start.strftime("%Y-%m") for month_start in only_month_starts]

    month_filter_sql = ""
    month_params: list = []
    month_ranges = []
    if only_month_starts:
        for month_start in only_month_starts:
            next_month = (
                datetime.date(month_start.year + 1, 1, 1)
                if month_start.month == 12
                else datetime.date(month_start.year, month_start.month + 1, 1)
            )
            month_ranges.append((month_start, next_month))

    scoped = DashboardAsinMonthlySummary.objects.filter(user=user)
    if month_ranges:
        from django.db.models import Q
        month_q = Q()
        for month_start, next_month in month_ranges:
            month_q |= Q(year_month__gte=month_start, year_month__lt=next_month)
        scoped = scoped.filter(month_q)
    scoped.delete()

    if month_ranges:
        clauses = []
        for month_start, next_month in month_ranges:
            clauses.append("(date >= %s AND date < %s)")
            month_params.extend([month_start, next_month])
        month_filter_sql = f" AND ({' OR '.join(clauses)})"

    tbl = DashboardAsinMonthlySummary._meta.db_table
    az_tbl = ProcessedDashboardData._meta.db_table
    fk_tbl = FlipkartProcessedDashboardData._meta.db_table

    az_sql = f"""
        INSERT INTO `{tbl}` (
            `user_id`, `platform`, `asin`, `year_month`,
            `portfolio`, `category`, `subcategory`,
            `revenue`, `orders`, `units`, `pageviews`,
            `total_spend`, `spend_sp`, `spend_sb`, `spend_sd`
        )
        SELECT
            user_id,
            'Amazon',
            asin,
            DATE_FORMAT(date, '%%Y-%%m-01'),
            COALESCE(portfolio, ''),
            COALESCE(category, ''),
            COALESCE(subcategory, ''),
            SUM(revenue),
            SUM(orders),
            SUM(units),
            SUM(pageviews),
            SUM(total_spend),
            SUM(spend_sp),
            SUM(spend_sb),
            SUM(spend_sd)
        FROM `{az_tbl}`
        WHERE user_id = %s{month_filter_sql}
        GROUP BY user_id, asin,
                 DATE_FORMAT(date, '%%Y-%%m-01'),
                 COALESCE(portfolio, ''),
                 COALESCE(category, ''),
                 COALESCE(subcategory, '')
    """

    fk_sql = f"""
        INSERT INTO `{tbl}` (
            `user_id`, `platform`, `asin`, `year_month`,
            `portfolio`, `category`, `subcategory`,
            `revenue`, `orders`, `units`, `pageviews`,
            `total_spend`, `spend_sp`, `spend_sb`, `spend_sd`
        )
        SELECT
            user_id,
            'Flipkart',
            fsn,
            DATE_FORMAT(date, '%%Y-%%m-01'),
            COALESCE(portfolio, ''),
            COALESCE(category, ''),
            COALESCE(subcategory, ''),
            SUM(revenue),
            SUM(orders),
            SUM(units),
            SUM(pageviews),
            SUM(total_spend),
            SUM(spend_sp),
            SUM(spend_sb),
            SUM(spend_sd)
        FROM `{fk_tbl}`
        WHERE user_id = %s{month_filter_sql}
        GROUP BY user_id, fsn,
                 DATE_FORMAT(date, '%%Y-%%m-01'),
                 COALESCE(portfolio, ''),
                 COALESCE(category, ''),
                 COALESCE(subcategory, '')
    """

    params = [user.id] + month_params
    rows_written = 0
    with connection.cursor() as cursor:
        cursor.execute(az_sql, params)
        rows_written += max(cursor.rowcount, 0)
        cursor.execute(fk_sql, params)
        rows_written += max(cursor.rowcount, 0)

    return {
        "rows_written": rows_written,
        "months_scoped": sorted(only_month_strs) if only_month_strs else [],
    }


# ---------------------------------------------------------------------------
# Date-range helpers
# ---------------------------------------------------------------------------

def _ym(d):
    """First day of the month for date d."""
    return d.replace(day=1)


def _ym_range_from_filters(filters):
    """
    Convert date filters to (ym_start, ym_end) for querying DashboardAsinMonthlySummary.

    Returns None when the range is < 45 days (monthly granularity adds no value)
    or when asin/fsn-level filters are active (monthly summary still supports these
    via direct asin column filters).

    year_month values are always the first day of the month.
    Note: boundaries are inclusive of the whole month, so a query for
    "last 3 months" will include the current partial month in its entirety.
    This slight over-count is acceptable for activity/product ranking queries.
    """
    today = timezone.localdate()
    date_range = (filters.get("date_range") or "").strip()

    if date_range == "last_3_months":
        ym_end = _ym(today)
        ym_start = _ym(today - datetime.timedelta(days=90))
        return ym_start, ym_end

    if date_range == "last_6_months":
        ym_end = _ym(today)
        ym_start = _ym(today - datetime.timedelta(days=180))
        return ym_start, ym_end

    if date_range == "last_1_year":
        ym_end = _ym(today)
        ym_start = _ym(today - datetime.timedelta(days=365))
        return ym_start, ym_end

    if date_range == "last_month":
        first_of_this = today.replace(day=1)
        last_month_end = first_of_this - datetime.timedelta(days=1)
        ym = _ym(last_month_end)
        return ym, ym

    # Custom date range — only use monthly summary for long spans (≥ 45 days).
    start_str = filters.get("start_date")
    end_str = filters.get("end_date")
    if start_str and end_str:
        try:
            start_d = datetime.datetime.strptime(str(start_str), "%Y-%m-%d").date()
            end_d = datetime.datetime.strptime(str(end_str), "%Y-%m-%d").date()
            if end_d < start_d:
                start_d, end_d = end_d, start_d
            if (end_d - start_d).days >= 45:
                return _ym(start_d), _ym(end_d)
        except Exception:
            pass

    return None  # too short or unknown → caller falls back to ProcessedDashboardData


def _apply_dimension_filters(qs, filters):
    """Apply category / portfolio / subcategory / asin / fsn filters to an AMS queryset."""
    category = filters.get("category")
    if category:
        if isinstance(category, (list, tuple)):
            qs = qs.filter(category__in=[str(c) for c in category])
        else:
            qs = qs.filter(category=str(category))

    portfolio = filters.get("portfolio")
    if portfolio:
        if isinstance(portfolio, (list, tuple)):
            qs = qs.filter(portfolio__in=[str(p) for p in portfolio])
        else:
            qs = qs.filter(portfolio=str(portfolio))

    subcategory = filters.get("subcategory")
    if subcategory:
        if isinstance(subcategory, (list, tuple)):
            qs = qs.filter(subcategory__in=[str(s) for s in subcategory])
        else:
            qs = qs.filter(subcategory=str(subcategory))

    asin_filter = filters.get("asin")
    if asin_filter:
        if isinstance(asin_filter, (list, tuple)):
            qs = qs.filter(asin__in=[str(a) for a in asin_filter])
        else:
            qs = qs.filter(asin=str(asin_filter))

    fsn_filter = filters.get("fsn")
    if fsn_filter:
        if isinstance(fsn_filter, (list, tuple)):
            qs = qs.filter(asin__in=[str(f) for f in fsn_filter])
        else:
            qs = qs.filter(asin=str(fsn_filter))

    return qs


def get_ams_qs(user, filters):
    """
    Return a filtered DashboardAsinMonthlySummary queryset or None.
    Returns None when:
      - date range is too short (< 45 days)
      - monthly summary has no data for this user
    """
    ym_range = _ym_range_from_filters(filters)
    if ym_range is None:
        return None

    ym_start, ym_end = ym_range
    qs = DashboardAsinMonthlySummary.objects.filter(
        user=user,
        year_month__gte=ym_start,
        year_month__lte=ym_end,
    )

    platform = (filters.get("platform") or "").strip()
    if platform == "Amazon":
        qs = qs.filter(platform="Amazon")
    elif platform == "Flipkart":
        qs = qs.filter(platform="Flipkart")

    qs = _apply_dimension_filters(qs, filters)
    return qs


# ---------------------------------------------------------------------------
# Activity metrics (replaces _compute_activity_metrics for long date ranges)
# ---------------------------------------------------------------------------

def compute_activity_metrics_from_monthly(user, filters, fsn_meta=None):
    """
    Fast alternative to _compute_activity_metrics / _compute_sku_activity_combined.
    Uses DashboardAsinMonthlySummary — 30× fewer rows for multi-month ranges.

    Returns the same dict as _compute_activity_metrics, or None if the monthly
    summary is not applicable (date range too short / no data yet).
    """
    ams_qs = get_ams_qs(user, filters)
    if ams_qs is None:
        return None

    platform_filter = (filters.get("platform") or "").strip()

    az_active = az_selling = az_zero_count = az_zero_pv = 0
    fk_active = fk_selling = fk_zero_count = fk_zero_pv = 0

    # GROUP BY asin on the small monthly summary instead of the daily table
    for row in (
        ams_qs.values("platform", "asin")
        .annotate(
            total_units=Sum("units"),
            total_pv=Sum("pageviews"),
            total_rev=Sum("revenue"),
            total_orders=Sum("orders"),
        )
        .iterator(chunk_size=5000)
    ):
        plat = row.get("platform") or ""
        has_units = (row.get("total_units") or 0) > 0
        has_activity = (
            (row.get("total_pv") or 0) > 0
            or (row.get("total_rev") or 0) > 0
            or (row.get("total_orders") or 0) > 0
        )

        if plat == "Amazon":
            az_active += 1
            if has_units:
                az_selling += 1
            elif has_activity:
                az_zero_count += 1
                az_zero_pv += int(row.get("total_pv") or 0)
        else:
            fk_active += 1
            if has_units:
                fk_selling += 1
            elif has_activity:
                fk_zero_count += 1
                fk_zero_pv += int(row.get("total_pv") or 0)

    if az_active + fk_active == 0:
        return None  # monthly summary has no rows for this filter — fall back to raw table

    # Flipkart continue / discontinue revenue (uses pre-cached fsn_meta — no extra DB hit)
    status_counts = {"Continued": 0, "Discontinued": 0}
    status_revenue = {"Continued": 0.0, "Discontinued": 0.0}

    if fsn_meta and platform_filter != "Amazon":
        cat_f = filters.get("category")
        port_f = filters.get("portfolio")
        sub_f = filters.get("subcategory")
        fsn_f = filters.get("fsn")

        def _matches(val, sel):
            if not sel:
                return True
            return val in sel if isinstance(sel, (list, tuple, set)) else val == sel

        fsn_to_status: dict = {}
        for fsn, meta in fsn_meta.items():
            if not _matches(meta.get("category", ""), cat_f):
                continue
            if not _matches(meta.get("portfolio", ""), port_f):
                continue
            if not _matches(meta.get("subcategory", ""), sub_f):
                continue
            if fsn_f and not _matches(fsn, fsn_f):
                continue
            raw = str(meta.get("product_status") or "").strip().lower()
            if raw in ("continued", "continue", "continued/pack of not sales"):
                fsn_to_status[fsn] = "Continued"
            elif raw in ("discontinued", "discontinue"):
                fsn_to_status[fsn] = "Discontinued"

        for status in fsn_to_status.values():
            if status in status_counts:
                status_counts[status] += 1

        fk_ams_qs = ams_qs.filter(platform="Flipkart")
        for row in (
            fk_ams_qs.values("asin")
            .annotate(revenue=Sum("revenue"))
            .iterator(chunk_size=5000)
        ):
            fsn = row.get("asin")
            if not fsn:
                continue
            status = fsn_to_status.get(str(fsn).strip())
            if status in status_revenue:
                status_revenue[status] += float(row.get("revenue") or 0.0)

    return {
        "active_asins": az_active + fk_active,
        "selling_sku_count": az_selling + fk_selling,
        "zero_selling_sku_count": az_zero_count + fk_zero_count,
        "zero_sales_pageviews": az_zero_pv + fk_zero_pv,
        "az_selling_sku_count": az_selling,
        "fk_selling_sku_count": fk_selling,
        "az_zero_selling_sku_count": az_zero_count,
        "fk_zero_selling_sku_count": fk_zero_count,
        "az_zero_sales_pageviews": az_zero_pv,
        "fk_zero_sales_pageviews": fk_zero_pv,
        "continue_sales_revenue": round(status_revenue["Continued"], 2),
        "discontinue_sales_revenue": round(status_revenue["Discontinued"], 2),
        "continue_sku_count": int(status_counts["Continued"]),
        "discontinued_sku_count": int(status_counts["Discontinued"]),
    }


# ---------------------------------------------------------------------------
# Top products (replaces _build_top_product_rows for long date ranges)
# ---------------------------------------------------------------------------

def build_top_products_from_monthly(
    user,
    filters,
    asin_meta=None,
    fsn_meta=None,
    limit=5,
    include_full_payload=False,
):
    """
    Fast top-products list from DashboardAsinMonthlySummary.
    Returns a list of product dicts (same shape as _build_top_product_rows),
    or None if the monthly summary is not applicable.

    Growth vs. previous period is computed with a second aggregation on the
    monthly table (still much faster than querying ProcessedDashboardData).
    """
    ams_qs = get_ams_qs(user, filters)
    if ams_qs is None:
        return None

    effective_limit = None if include_full_payload else limit

    # Current-period per-ASIN totals
    current_agg = (
        ams_qs.values("asin", "platform")
        .annotate(revenue=Sum("revenue"), units=Sum("units"))
        .order_by("-revenue")
    )
    if effective_limit:
        current_agg = current_agg[:effective_limit]

    current_rows = list(current_agg)
    if not current_rows:
        return None

    # Previous-period revenue — same year_month range shifted back one month
    ym_range = _ym_range_from_filters(filters)
    prev_revenue_by_asin: dict = {}
    if ym_range:
        ym_start, ym_end = ym_range
        span_days = (ym_end - ym_start).days + 31  # approx span in days
        prev_ym_end = _ym(ym_start - datetime.timedelta(days=1))
        prev_ym_start = _ym(prev_ym_end - datetime.timedelta(days=span_days))

        asin_ids = [str(r["asin"]) for r in current_rows]
        prev_qs = DashboardAsinMonthlySummary.objects.filter(
            user=user,
            asin__in=asin_ids,
            year_month__gte=prev_ym_start,
            year_month__lte=prev_ym_end,
        )
        platform = (filters.get("platform") or "").strip()
        if platform == "Amazon":
            prev_qs = prev_qs.filter(platform="Amazon")
        elif platform == "Flipkart":
            prev_qs = prev_qs.filter(platform="Flipkart")

        for row in prev_qs.values("asin").annotate(revenue=Sum("revenue")):
            asin = str(row.get("asin") or "")
            if asin:
                prev_revenue_by_asin[asin] = float(row.get("revenue") or 0.0)

    from apps.dashboard.services.metrics import safe_growth as _safe_growth

    results = []
    for row in current_rows:
        asin = str(row.get("asin") or "").strip()
        if not asin:
            continue
        plat = row.get("platform") or ""
        meta = (asin_meta or {}).get(asin, {}) if plat == "Amazon" else (fsn_meta or {}).get(asin, {})
        curr_rev = float(row.get("revenue") or 0.0)
        results.append({
            "sku": asin,
            "cluster": meta.get("portfolio") or "Standard",
            "revenue": curr_rev,
            "growth": _safe_growth(curr_rev, prev_revenue_by_asin.get(asin, 0.0)),
            "units_sold": int(row.get("units") or 0),
        })

    results.sort(key=lambda r: r["revenue"], reverse=True)
    return results


# ---------------------------------------------------------------------------
# Declining products (replaces _build_declining_product_rows for long ranges)
# ---------------------------------------------------------------------------

def build_declining_products_from_monthly(
    user,
    filters,
    cm_start,
    cm_end,
    pm_start,
    pm_end,
    include_full_payload=False,
):
    """
    Fast declining-product list using DashboardAsinMonthlySummary.
    Compares current-month-period revenue vs. previous-month-period revenue.
    Returns None if monthly summary is not applicable.
    """
    ams_qs = get_ams_qs(user, filters)
    if ams_qs is None:
        return None

    # Expand the date range to cover both periods
    period_min_ym = _ym(min(cm_start, pm_start))
    period_max_ym = _ym(max(cm_end, pm_end))
    cm_ym_start = _ym(cm_start)
    cm_ym_end = _ym(cm_end)
    pm_ym_start = _ym(pm_start)
    pm_ym_end = _ym(pm_end)

    base_qs = DashboardAsinMonthlySummary.objects.filter(
        user=user,
        year_month__gte=period_min_ym,
        year_month__lte=period_max_ym,
    )
    platform = (filters.get("platform") or "").strip()
    if platform == "Amazon":
        base_qs = base_qs.filter(platform="Amazon")
    elif platform == "Flipkart":
        base_qs = base_qs.filter(platform="Flipkart")
    base_qs = _apply_dimension_filters(base_qs, filters)

    from django.db.models import Case, F, Value, When, FloatField

    cm_sku_rev: dict = {}
    pm_sku_rev: dict = {}

    for row in (
        base_qs.values("asin")
        .annotate(
            cm_r=Sum(
                Case(
                    When(year_month__gte=cm_ym_start, year_month__lte=cm_ym_end, then=F("revenue")),
                    default=Value(0.0),
                    output_field=FloatField(),
                )
            ),
            pm_r=Sum(
                Case(
                    When(year_month__gte=pm_ym_start, year_month__lte=pm_ym_end, then=F("revenue")),
                    default=Value(0.0),
                    output_field=FloatField(),
                )
            ),
        )
        .iterator(chunk_size=5000)
    ):
        asin = str(row.get("asin") or "").strip()
        if not asin:
            continue
        if row.get("cm_r"):
            cm_sku_rev[asin] = float(row.get("cm_r") or 0.0)
        if row.get("pm_r"):
            pm_sku_rev[asin] = float(row.get("pm_r") or 0.0)

    if not cm_sku_rev and not pm_sku_rev:
        return None  # monthly summary empty for this period — fall back to ProcessedDashboardData

    from apps.dashboard.services.metrics import safe_growth as _safe_growth

    declining = []
    for asin in set(cm_sku_rev) | set(pm_sku_rev):
        curr = cm_sku_rev.get(asin, 0.0)
        prev = pm_sku_rev.get(asin, 0.0)
        drop_pct = _safe_growth(curr, prev)
        if drop_pct < 0:
            declining.append({
                "sku": asin,
                "revenue": curr,
                "drop_pct": drop_pct,
                "impact": max(prev - curr, 0.0),
            })

    declining.sort(key=lambda r: (r["revenue"], r["drop_pct"]))
    return declining if include_full_payload else declining[:5]
