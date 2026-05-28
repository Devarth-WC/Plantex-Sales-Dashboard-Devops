"""
Export service: builds calculated export data from processed dashboard tables
and returns CSV / Excel (with Annexure sheet).
"""

from io import BytesIO

import pandas as pd
from django.db.models import Max, Sum

from apps.dashboard.models import FlipkartProcessedDashboardData, ProcessedDashboardData
from apps.dashboard.services.filters import apply_dashboard_entity_filters
from apps.dashboard.services.analytics_services_orm_pipeline import apply_global_filters_orm
from apps.dashboard.services.metrics import (
    GST_REVENUE_FACTOR,
    amazon_cvr,
    flipkart_cvr,
    roas,
    tacos,
)


def _get_filtered_querysets(user, filters):
    """Return filtered Amazon + Flipkart querysets for the current user."""
    data_owner = user.created_by if user.created_by else user

    qs = ProcessedDashboardData.objects.filter(user=data_owner)
    fk_qs = FlipkartProcessedDashboardData.objects.filter(user=data_owner)

    qs, fk_qs = apply_dashboard_entity_filters(qs, fk_qs, filters)
    qs = apply_global_filters_orm(qs, filters)
    fk_qs = apply_global_filters_orm(fk_qs, filters)

    return qs, fk_qs


def _build_amazon_export_dataframe(qs):
    col_order = [
        "Platform",
        "ASIN",
        "Portfolio",
        "Category",
        "Subcategory",
        "Page Views",
        "Units",
        "Orders",
        "Revenue",
        "Price",
        "Spend",
        "Spend (SP)",
        "Spend (SB)",
        "Spend (SD)",
        "ROAS",
        "TACoS (%)",
        "CVR (%)",
    ]
    rows = []
    aggregate_rows = (
        qs.values("asin", "portfolio", "category", "subcategory")
        .annotate(
            pageviews=Sum("pageviews"),
            units=Sum("units"),
            orders=Sum("orders"),
            revenue=Sum("revenue"),
            spend_sp=Sum("spend_sp"),
            spend_sb=Sum("spend_sb"),
            spend_sd=Sum("spend_sd"),
            total_spend=Sum("total_spend"),
            price=Max("price"),
        )
        .iterator(chunk_size=5000)
    )
    for row in aggregate_rows:
        revenue = float(row.get("revenue") or 0)
        spend = float(row.get("total_spend") or 0)
        orders = int(row.get("orders") or 0)
        pageviews = int(row.get("pageviews") or 0)
        rows.append(
            {
                "Platform": "Amazon",
                "ASIN": row.get("asin"),
                "Portfolio": row.get("portfolio") or "Unknown",
                "Category": row.get("category") or "Unknown",
                "Subcategory": row.get("subcategory") or "Unknown",
                "Page Views": pageviews,
                "Units": int(row.get("units") or 0),
                "Orders": orders,
                "Revenue": revenue,
                "Price": float(row.get("price") or 0),
                "Spend": spend,
                "Spend (SP)": float(row.get("spend_sp") or 0),
                "Spend (SB)": float(row.get("spend_sb") or 0),
                "Spend (SD)": float(row.get("spend_sd") or 0),
                "ROAS": round(roas(revenue, spend), 2),
                "TACoS (%)": round(tacos(revenue, spend), 2),
                "CVR (%)": round(amazon_cvr(orders, pageviews), 2),
            }
        )
    return pd.DataFrame(rows, columns=col_order)


def _build_flipkart_export_dataframe(fk_qs):
    col_order = [
        "Platform",
        "FSN",
        "Portfolio",
        "Category",
        "Subcategory",
        "Page Views",
        "Units Sold",
        "Revenue",
        "Price",
        "Ad Spend",
        "ROAS",
        "TACoS (%)",
        "CVR",
    ]
    rows = []
    aggregate_rows = (
        fk_qs.values("fsn", "portfolio", "category", "subcategory")
        .annotate(
            pageviews=Sum("pageviews"),
            units=Sum("units"),
            revenue=Sum("revenue"),
            total_spend=Sum("total_spend"),
            price=Max("price"),
        )
        .iterator(chunk_size=5000)
    )
    for row in aggregate_rows:
        revenue = float(row.get("revenue") or 0)
        spend = float(row.get("total_spend") or 0)
        pageviews = int(row.get("pageviews") or 0)
        units = int(row.get("units") or 0)
        rows.append(
            {
                "Platform": "Flipkart",
                "FSN": row.get("fsn"),
                "Portfolio": row.get("portfolio") or "Unknown",
                "Category": row.get("category") or "Unknown",
                "Subcategory": row.get("subcategory") or "Unknown",
                "Page Views": pageviews,
                "Units Sold": units,
                "Revenue": revenue,
                "Price": float(row.get("price") or 0),
                "Ad Spend": spend,
                "ROAS": round(roas(revenue, spend), 2),
                "TACoS (%)": round(tacos(revenue, spend), 2),
                "CVR": round(flipkart_cvr(units, pageviews), 2),
            }
        )
    return pd.DataFrame(rows, columns=col_order)


AMAZON_ANNEXURE_DATA = [
    {
        "Metric": "ROAS",
        "Formula": f"(Revenue × {GST_REVENUE_FACTOR}) / Spend",
        "Description": "Return on Ad Spend based on GST-adjusted revenue.",
    },
    {
        "Metric": "TACoS (%)",
        "Formula": f"(Spend / (Revenue × {GST_REVENUE_FACTOR})) * 100",
        "Description": "Total Advertising Cost of Sale as percentage of revenue.",
    },
    {
        "Metric": "CVR (%)",
        "Formula": "(Orders / Page Views) * 100",
        "Description": "Conversion Rate from page views to orders.",
    },
]


FLIPKART_ANNEXURE_DATA = [
    {
        "Metric": "ROAS",
        "Formula": f"(Revenue × {GST_REVENUE_FACTOR}) / Ad Spend",
        "Description": "Return on Ad Spend based on GST-adjusted revenue.",
    },
    {
        "Metric": "TACoS (%)",
        "Formula": f"(Ad Spend / (Revenue × {GST_REVENUE_FACTOR})) * 100",
        "Description": "Total ad spend as percentage of GST-adjusted revenue.",
    },
    {
        "Metric": "CVR",
        "Formula": "(Units Sold / Page Views) * 100",
        "Description": "Flipkart conversion rate from page views to units sold.",
    },
]


def _build_export_payload(user, filters):
    """
    Build export dataframe + annexure based on selected platform.
    For platform="All", combines both datasets.
    """
    qs, fk_qs = _get_filtered_querysets(user, filters)
    platform = filters.get("platform")

    if platform == "Amazon":
        return _build_amazon_export_dataframe(qs), pd.DataFrame(AMAZON_ANNEXURE_DATA)

    if platform == "Flipkart":
        return _build_flipkart_export_dataframe(fk_qs), pd.DataFrame(FLIPKART_ANNEXURE_DATA)

    df_amz = _build_amazon_export_dataframe(qs)
    df_fk = _build_flipkart_export_dataframe(fk_qs)

    if df_amz.empty and df_fk.empty:
        return pd.DataFrame(), pd.DataFrame(AMAZON_ANNEXURE_DATA + FLIPKART_ANNEXURE_DATA)
    if df_amz.empty:
        return df_fk, pd.DataFrame(FLIPKART_ANNEXURE_DATA)
    if df_fk.empty:
        return df_amz, pd.DataFrame(AMAZON_ANNEXURE_DATA)

    return (
        pd.concat([df_amz, df_fk], ignore_index=True, sort=False).fillna(""),
        pd.DataFrame(AMAZON_ANNEXURE_DATA + FLIPKART_ANNEXURE_DATA),
    )


def export_csv(user, filters):
    """Return a BytesIO buffer containing the calculated CSV."""
    df, _ = _build_export_payload(user, filters)
    buf = BytesIO()
    if df.empty:
        buf.write(b"No data available for the selected filters.\n")
    else:
        df.to_csv(buf, index=False)
    buf.seek(0)
    return buf


def export_excel(user, filters):
    """Return a BytesIO buffer containing the calculated Excel with Annexure."""
    df, annexure_df = _build_export_payload(user, filters)
    buf = BytesIO()
    with pd.ExcelWriter(buf, engine="openpyxl") as writer:
        if df.empty:
            pd.DataFrame({"Message": ["No data available for the selected filters."]}).to_excel(
                writer, sheet_name="Data", index=False
            )
        else:
            df.to_excel(writer, sheet_name="Data", index=False)
        annexure_df.to_excel(writer, sheet_name="Annexure", index=False)
    buf.seek(0)
    return buf
