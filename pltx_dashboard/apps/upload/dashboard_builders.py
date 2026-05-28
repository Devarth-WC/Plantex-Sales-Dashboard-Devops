import datetime
import logging
import time

from django.db import connection
from django.db.models import Sum

from apps.dashboard.models import (
    CategoryMapping,
    FlipkartCategoryMap,
    FlipkartPLA,
    FlipkartPrice,
    FlipkartProcessedDashboardData,
    FlipkartSearchTraffic,
    PriceData,
    ProcessedDashboardData,
    SalesData,
    SpendData,
)
from apps.dashboard.services.invalidation import invalidate_dashboard_cache_for_user
from apps.dashboard.utils import clean_number

from .service_common import DB_BATCH_SIZE

logger = logging.getLogger(__name__)


def _notify_progress(progress_callback, message):
    if progress_callback:
        try:
            progress_callback(message)
        except Exception:
            pass


def _safe_float(value):
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return 0.0


def _safe_int(value):
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return clean_number(value)


def _parse_target_dates(only_dates):
    target_dates = set()
    if only_dates:
        for value in only_dates:
            if isinstance(value, datetime.datetime):
                target_dates.add(value.date())
            elif isinstance(value, datetime.date):
                target_dates.add(value)
            elif isinstance(value, str) and value.strip():
                try:
                    parsed = datetime.datetime.strptime(value.strip(), "%Y-%m-%d").date()
                    target_dates.add(parsed)
                except ValueError:
                    pass
    return target_dates


def _invalidate_user_dashboard_cache(user_id):
    invalidate_dashboard_cache_for_user(user_id, clear_materialized=True)


def _normalize_identifier_values(values):
    cleaned = []
    seen = set()
    for value in values or []:
        item = str(value or "").strip()
        if not item or item in seen:
            continue
        seen.add(item)
        cleaned.append(item)
    return cleaned


def _identifier_batches(values, batch_size=DB_BATCH_SIZE):
    normalized = _normalize_identifier_values(values)
    for idx in range(0, len(normalized), batch_size):
        yield normalized[idx : idx + batch_size]


def _mysql_insert_processed_dashboard_rows(user_id, target_dates):
    """
    Build ProcessedDashboardData fully in MySQL using INSERT...SELECT joins.
    This uses an upsert strategy: first insert sales data, then upsert spend 
    aggregates. This avoids slow materializing subqueries and grouping.
    """
    sales_table = SalesData._meta.db_table
    spend_table = SpendData._meta.db_table
    category_table = CategoryMapping._meta.db_table
    price_table = PriceData._meta.db_table
    processed_table = ProcessedDashboardData._meta.db_table

    target_dates = sorted(target_dates or [])
    date_placeholders = ", ".join(["%s"] * len(target_dates)) if target_dates else ""
    sales_date_filter = f" AND s.date IN ({date_placeholders})" if target_dates else ""
    spend_date_filter = f" AND date IN ({date_placeholders})" if target_dates else ""

    insert_sales_sql = f"""
        INSERT INTO {processed_table} (
            user_id, date, asin, portfolio, category, subcategory, price,
            pageviews, units, orders, revenue, spend_sp, spend_sb, spend_sd, total_spend
        )
        SELECT
            %s AS user_id,
            s.date,
            s.asin,
            COALESCE(cm.portfolio, '') AS portfolio,
            COALESCE(cm.category, '') AS category,
            COALESCE(cm.subcategory, '') AS subcategory,
            COALESCE(pd.price, 0) AS price,
            COALESCE(s.pageviews, 0) AS pageviews,
            COALESCE(s.units, 0) AS units,
            COALESCE(s.orders, 0) AS orders,
            COALESCE(s.revenue, 0) AS revenue,
            0 AS spend_sp,
            0 AS spend_sb,
            0 AS spend_sd,
            0 AS total_spend
        FROM {sales_table} s
        LEFT JOIN {category_table} cm
            ON cm.user_id = %s AND cm.asin = s.asin
        LEFT JOIN {price_table} pd
            ON pd.user_id = %s AND pd.asin = s.asin
        WHERE s.user_id = %s {sales_date_filter}
    """

    upsert_spend_sql = f"""
        INSERT INTO {processed_table} (
            user_id, date, asin, portfolio, category, subcategory, price,
            pageviews, units, orders, revenue, spend_sp, spend_sb, spend_sd, total_spend
        )
        SELECT
            %s AS user_id,
            sp.date,
            sp.asin,
            COALESCE(cm.portfolio, '') AS portfolio,
            COALESCE(cm.category, '') AS category,
            COALESCE(cm.subcategory, '') AS subcategory,
            COALESCE(pd.price, 0) AS price,
            0 AS pageviews,
            0 AS units,
            0 AS orders,
            0 AS revenue,
            sp.spend_sp,
            sp.spend_sb,
            sp.spend_sd,
            sp.spend_sp + sp.spend_sb + sp.spend_sd AS total_spend
        FROM (
            SELECT
                date,
                asin,
                SUM(CASE WHEN UPPER(ad_type) = 'SP' THEN spend ELSE 0 END) AS spend_sp,
                SUM(CASE WHEN UPPER(ad_type) = 'SB' THEN spend ELSE 0 END) AS spend_sb,
                SUM(CASE WHEN UPPER(ad_type) = 'SD' THEN spend ELSE 0 END) AS spend_sd
            FROM {spend_table}
            WHERE user_id = %s {spend_date_filter}
            GROUP BY date, asin
        ) sp
        LEFT JOIN {category_table} cm
            ON cm.user_id = %s AND cm.asin = sp.asin
        LEFT JOIN {price_table} pd
            ON pd.user_id = %s AND pd.asin = sp.asin
        ON DUPLICATE KEY UPDATE
            spend_sp = VALUES(spend_sp),
            spend_sb = VALUES(spend_sb),
            spend_sd = VALUES(spend_sd),
            total_spend = VALUES(total_spend)
    """

    sales_params = [user_id, user_id, user_id, user_id]
    if target_dates:
        sales_params.extend(target_dates)

    spend_params = [user_id, user_id]
    if target_dates:
        spend_params.extend(target_dates)
    spend_params.extend([user_id, user_id])

    rows_written = 0
    with connection.cursor() as cursor:
        cursor.execute(insert_sales_sql, sales_params)
        rows_written += max(int(cursor.rowcount or 0), 0)
        cursor.execute(upsert_spend_sql, spend_params)
        # Note: ON DUPLICATE KEY UPDATE might return 2 for updated rows, 
        # so rowcount isn't purely "new rows". We return it for logging.
        rows_written += max(int(cursor.rowcount or 0), 0)

    return rows_written


def update_category_in_processed_data(user_id, asins=None):
    """
    In-place UPDATE: refreshes portfolio/category/subcategory in ProcessedDashboardData
    from the latest CategoryMapping. Much faster than DELETE + full re-INSERT.
    """
    p_tbl = ProcessedDashboardData._meta.db_table
    c_tbl = CategoryMapping._meta.db_table
    total_rows = 0
    scoped_batches = list(_identifier_batches(asins))
    if not scoped_batches:
        scoped_batches = [None]

    with connection.cursor() as cursor:
        for asin_batch in scoped_batches:
            where_sql = "WHERE p.user_id = %s"
            params = [user_id, user_id]
            if asin_batch is not None:
                placeholders = ", ".join(["%s"] * len(asin_batch))
                where_sql += f" AND p.asin IN ({placeholders})"
                params.extend(asin_batch)
            cursor.execute(f"""
                UPDATE `{p_tbl}` p
                LEFT JOIN `{c_tbl}` cm ON cm.user_id = %s AND cm.asin = p.asin
                SET p.portfolio   = COALESCE(cm.portfolio, ''),
                    p.category    = COALESCE(cm.category, ''),
                    p.subcategory = COALESCE(cm.subcategory, '')
                {where_sql}
            """, params)
            total_rows += max(int(cursor.rowcount or 0), 0)
    logger.info("[Dashboard] category in-place UPDATE user=%s rows=%d scoped=%s", user_id, total_rows, bool(asins))
    return total_rows


def update_price_in_processed_data(user_id, asins=None):
    """
    In-place UPDATE: refreshes price in ProcessedDashboardData
    from the latest PriceData. Much faster than DELETE + full re-INSERT.
    """
    p_tbl = ProcessedDashboardData._meta.db_table
    pr_tbl = PriceData._meta.db_table
    total_rows = 0
    scoped_batches = list(_identifier_batches(asins))
    if not scoped_batches:
        scoped_batches = [None]

    with connection.cursor() as cursor:
        for asin_batch in scoped_batches:
            where_sql = "WHERE p.user_id = %s"
            params = [user_id, user_id]
            if asin_batch is not None:
                placeholders = ", ".join(["%s"] * len(asin_batch))
                where_sql += f" AND p.asin IN ({placeholders})"
                params.extend(asin_batch)
            cursor.execute(f"""
                UPDATE `{p_tbl}` p
                LEFT JOIN `{pr_tbl}` pr ON pr.user_id = %s AND pr.asin = p.asin
                SET p.price = COALESCE(pr.price, 0.0)
                {where_sql}
            """, params)
            total_rows += max(int(cursor.rowcount or 0), 0)
    logger.info("[Dashboard] price in-place UPDATE user=%s rows=%d scoped=%s", user_id, total_rows, bool(asins))
    return total_rows


def update_fk_category_in_processed_data(user_id, fsns=None):
    """
    In-place UPDATE: refreshes portfolio/category/subcategory in
    FlipkartProcessedDashboardData from the latest FlipkartCategoryMap.
    """
    p_tbl = FlipkartProcessedDashboardData._meta.db_table
    c_tbl = FlipkartCategoryMap._meta.db_table
    total_rows = 0
    scoped_batches = list(_identifier_batches(fsns))
    if not scoped_batches:
        scoped_batches = [None]

    with connection.cursor() as cursor:
        for fsn_batch in scoped_batches:
            where_sql = "WHERE p.user_id = %s"
            params = [user_id, user_id]
            if fsn_batch is not None:
                placeholders = ", ".join(["%s"] * len(fsn_batch))
                where_sql += f" AND p.fsn IN ({placeholders})"
                params.extend(fsn_batch)
            cursor.execute(f"""
                UPDATE `{p_tbl}` p
                LEFT JOIN `{c_tbl}` cm ON cm.user_id = %s AND cm.fsn = p.fsn
                SET p.portfolio   = COALESCE(cm.portfolio, ''),
                    p.category    = COALESCE(cm.category, ''),
                    p.subcategory = COALESCE(cm.subcategory, '')
                {where_sql}
            """, params)
            total_rows += max(int(cursor.rowcount or 0), 0)
    logger.info("[Dashboard] fk_category in-place UPDATE user=%s rows=%d scoped=%s", user_id, total_rows, bool(fsns))
    return total_rows


def update_fk_price_in_processed_data(user_id, fsns=None):
    """
    In-place UPDATE: refreshes price in FlipkartProcessedDashboardData
    from the latest FlipkartPrice.
    """
    p_tbl = FlipkartProcessedDashboardData._meta.db_table
    pr_tbl = FlipkartPrice._meta.db_table
    total_rows = 0
    scoped_batches = list(_identifier_batches(fsns))
    if not scoped_batches:
        scoped_batches = [None]

    with connection.cursor() as cursor:
        for fsn_batch in scoped_batches:
            where_sql = "WHERE p.user_id = %s"
            params = [user_id, user_id]
            if fsn_batch is not None:
                placeholders = ", ".join(["%s"] * len(fsn_batch))
                where_sql += f" AND p.fsn IN ({placeholders})"
                params.extend(fsn_batch)
            cursor.execute(f"""
                UPDATE `{p_tbl}` p
                LEFT JOIN `{pr_tbl}` pr ON pr.user_id = %s AND pr.fsn = p.fsn
                SET p.price = COALESCE(pr.price, 0.0)
                {where_sql}
            """, params)
            total_rows += max(int(cursor.rowcount or 0), 0)
    logger.info("[Dashboard] fk_price in-place UPDATE user=%s rows=%d scoped=%s", user_id, total_rows, bool(fsns))
    return total_rows


def update_inventory_category_in_summary(user_id, asins=None):
    """
    In-place UPDATE for Amazon inventory-health metadata.
    Category uploads do not change stock/DOC math, so refreshing labels here
    avoids rebuilding all DashboardInventoryHealthSummary rows.
    """
    from apps.dashboard.models import DashboardInventoryHealthSummary

    inv_tbl = DashboardInventoryHealthSummary._meta.db_table
    cm_tbl = CategoryMapping._meta.db_table
    total_rows = 0
    scoped_batches = list(_identifier_batches(asins))
    if not scoped_batches:
        scoped_batches = [None]

    with connection.cursor() as cursor:
        for asin_batch in scoped_batches:
            where_sql = "WHERE ih.user_id = %s AND ih.platform = 'Amazon'"
            params = [user_id, user_id]
            if asin_batch is not None:
                placeholders = ", ".join(["%s"] * len(asin_batch))
                where_sql += f" AND ih.sku IN ({placeholders})"
                params.extend(asin_batch)
            cursor.execute(f"""
                UPDATE `{inv_tbl}` ih
                LEFT JOIN `{cm_tbl}` cm ON cm.user_id = %s AND cm.asin = ih.sku
                SET ih.portfolio   = COALESCE(cm.portfolio, ''),
                    ih.category    = COALESCE(cm.category, 'Unknown'),
                    ih.subcategory = COALESCE(cm.subcategory, '')
                {where_sql}
            """, params)
            total_rows += max(int(cursor.rowcount or 0), 0)
    logger.info("[Dashboard] inventory category UPDATE user=%s rows=%d scoped=%s", user_id, total_rows, bool(asins))
    return total_rows


def update_fk_inventory_category_in_summary(user_id, fsns=None):
    """
    In-place UPDATE for Flipkart inventory-health metadata.
    """
    from apps.dashboard.models import DashboardInventoryHealthSummary

    inv_tbl = DashboardInventoryHealthSummary._meta.db_table
    cm_tbl = FlipkartCategoryMap._meta.db_table
    total_rows = 0
    scoped_batches = list(_identifier_batches(fsns))
    if not scoped_batches:
        scoped_batches = [None]

    with connection.cursor() as cursor:
        for fsn_batch in scoped_batches:
            where_sql = "WHERE ih.user_id = %s AND ih.platform = 'Flipkart'"
            params = [user_id, user_id]
            if fsn_batch is not None:
                placeholders = ", ".join(["%s"] * len(fsn_batch))
                where_sql += f" AND ih.sku IN ({placeholders})"
                params.extend(fsn_batch)
            cursor.execute(f"""
                UPDATE `{inv_tbl}` ih
                LEFT JOIN `{cm_tbl}` cm ON cm.user_id = %s AND cm.fsn = ih.sku
                SET ih.portfolio   = COALESCE(cm.portfolio, ''),
                    ih.category    = COALESCE(cm.category, 'Unknown'),
                    ih.subcategory = COALESCE(cm.subcategory, '')
                {where_sql}
            """, params)
            total_rows += max(int(cursor.rowcount or 0), 0)
    logger.info("[Dashboard] fk inventory category UPDATE user=%s rows=%d scoped=%s", user_id, total_rows, bool(fsns))
    return total_rows


def _generate_dashboard_data_python(user, sales_qs, spend_qs, progress_callback):
    _notify_progress(progress_callback, "Loading category and price mappings...")
    category_by_asin = {}
    for row in CategoryMapping.objects.filter(user=user).values(
        "asin", "portfolio", "category", "subcategory"
    ).iterator(chunk_size=DB_BATCH_SIZE):
        asin = str(row.get("asin") or "").strip()
        if not asin:
            continue
        category_by_asin[asin] = (
            str(row.get("portfolio") or ""),
            str(row.get("category") or ""),
            str(row.get("subcategory") or ""),
        )

    price_by_asin = {}
    for row in PriceData.objects.filter(user=user).values("asin", "price").iterator(
        chunk_size=DB_BATCH_SIZE
    ):
        asin = str(row.get("asin") or "").strip()
        if not asin:
            continue
        price_by_asin[asin] = _safe_float(row.get("price"))

    _notify_progress(progress_callback, "Aggregating ad spend...")
    spend_by_key = {}
    spend_rows = (
        spend_qs.values("date", "asin", "ad_type")
        .annotate(spend_total=Sum("spend"))
        .iterator(chunk_size=DB_BATCH_SIZE)
    )
    for row in spend_rows:
        date = row.get("date")
        asin = str(row.get("asin") or "").strip()
        if not date or not asin:
            continue

        key = (date, asin)
        bucket = spend_by_key.setdefault(
            key, {"spend_sp": 0.0, "spend_sb": 0.0, "spend_sd": 0.0}
        )
        spend_total = _safe_float(row.get("spend_total"))
        ad_type = str(row.get("ad_type") or "").strip().upper()
        if ad_type == "SP":
            bucket["spend_sp"] += spend_total
        elif ad_type == "SB":
            bucket["spend_sb"] += spend_total
        elif ad_type == "SD":
            bucket["spend_sd"] += spend_total

    _notify_progress(progress_callback, "Building processed dashboard rows...")
    records = []
    total_rows = 0

    sales_rows = sales_qs.values(
        "date", "asin", "pageviews", "units", "orders", "revenue"
    ).iterator(chunk_size=DB_BATCH_SIZE)

    for row in sales_rows:
        date = row.get("date")
        asin = str(row.get("asin") or "").strip()
        if not date or not asin:
            continue

        spend_payload = spend_by_key.pop((date, asin), None)
        spend_sp = _safe_float(spend_payload.get("spend_sp")) if spend_payload else 0.0
        spend_sb = _safe_float(spend_payload.get("spend_sb")) if spend_payload else 0.0
        spend_sd = _safe_float(spend_payload.get("spend_sd")) if spend_payload else 0.0
        total_spend = spend_sp + spend_sb + spend_sd

        portfolio, category, subcategory = category_by_asin.get(asin, ("", "", ""))
        price = _safe_float(price_by_asin.get(asin, 0.0))

        records.append(
            ProcessedDashboardData(
                user=user,
                date=date,
                asin=asin,
                portfolio=portfolio,
                category=category,
                subcategory=subcategory,
                price=price,
                pageviews=_safe_int(row.get("pageviews")),
                units=_safe_int(row.get("units")),
                orders=_safe_int(row.get("orders")),
                revenue=_safe_float(row.get("revenue")),
                spend_sp=spend_sp,
                spend_sb=spend_sb,
                spend_sd=spend_sd,
                total_spend=total_spend,
            )
        )
        total_rows += 1

        if len(records) >= DB_BATCH_SIZE:
            ProcessedDashboardData.objects.bulk_create(records, ignore_conflicts=True)
            records = []

    # Keep spend-only rows (outer-join behavior).
    for (date, asin), spend_payload in spend_by_key.items():
        if not date or not asin:
            continue
        spend_sp = _safe_float(spend_payload.get("spend_sp"))
        spend_sb = _safe_float(spend_payload.get("spend_sb"))
        spend_sd = _safe_float(spend_payload.get("spend_sd"))
        total_spend = spend_sp + spend_sb + spend_sd
        portfolio, category, subcategory = category_by_asin.get(asin, ("", "", ""))
        price = _safe_float(price_by_asin.get(asin, 0.0))

        records.append(
            ProcessedDashboardData(
                user=user,
                date=date,
                asin=asin,
                portfolio=portfolio,
                category=category,
                subcategory=subcategory,
                price=price,
                pageviews=0,
                units=0,
                orders=0,
                revenue=0.0,
                spend_sp=spend_sp,
                spend_sb=spend_sb,
                spend_sd=spend_sd,
                total_spend=total_spend,
            )
        )
        total_rows += 1

        if len(records) >= DB_BATCH_SIZE:
            ProcessedDashboardData.objects.bulk_create(records, ignore_conflicts=True)
            records = []

    if records:
        ProcessedDashboardData.objects.bulk_create(records, ignore_conflicts=True)
    return total_rows


def generate_dashboard_data(user, progress_callback=None, only_dates=None):
    """
    Merges all independent Amazon tables for the given user and dumps them into
    ProcessedDashboardData to quickly serve the frontend.

    This implementation avoids loading giant DataFrames in memory, which keeps
    large historical uploads faster and more stable.
    """

    target_dates = _parse_target_dates(only_dates)

    _t0 = time.monotonic()
    mode = f"incremental ({len(target_dates)} dates)" if target_dates else "full rebuild"

    if target_dates:
        _notify_progress(progress_callback, "Refreshing dashboard aggregates for selected dates...")
    else:
        _notify_progress(progress_callback, "Refreshing dashboard aggregates...")

    processed_qs = ProcessedDashboardData.objects.filter(user=user)
    sales_qs = SalesData.objects.filter(user=user)
    spend_qs = SpendData.objects.filter(user=user)
    if target_dates:
        processed_qs = processed_qs.filter(date__in=target_dates)
        sales_qs = sales_qs.filter(date__in=target_dates)
        spend_qs = spend_qs.filter(date__in=target_dates)

    # Check data availability BEFORE deleting anything (avoid unnecessary
    # delete + empty insert cycles).
    has_sales = sales_qs.exists()
    has_spend = spend_qs.exists()
    if not has_sales and not has_spend:
        # Still delete stale processed rows for the scoped dates.
        processed_qs._raw_delete(processed_qs.db)
        _invalidate_user_dashboard_cache(user.id)
        return

    # For incremental mode: delete only the scoped date rows OUTSIDE the heavy
    # insert transaction so the table lock window is minimised.
    _notify_progress(progress_callback, "Clearing stale dashboard rows...")
    _t_del = time.monotonic()
    processed_qs._raw_delete(processed_qs.db)
    logger.info(
        "[Dashboard] delete phase user=%s mode=%s elapsed=%.1fs",
        user.id, mode, time.monotonic() - _t_del,
    )

    total_rows = 0
    _t_ins = time.monotonic()
    if connection.vendor == "mysql":
        _notify_progress(progress_callback, "Building processed dashboard rows...")
        total_rows = _mysql_insert_processed_dashboard_rows(
            user.id, sorted(target_dates)
        )
    else:
        total_rows = _generate_dashboard_data_python(
            user, sales_qs, spend_qs, progress_callback
        )
    logger.info(
        "[Dashboard] insert phase user=%s mode=%s rows=%d elapsed=%.1fs",
        user.id, mode, total_rows, time.monotonic() - _t_ins,
    )

    _notify_progress(progress_callback, f"Processed {total_rows} dashboard rows.")
    _invalidate_user_dashboard_cache(user.id)
    _elapsed = time.monotonic() - _t0
    logger.info(
        "[Dashboard] generate_dashboard_data user=%s mode=%s rows=%d elapsed=%.1fs",
        user.id, mode, total_rows, _elapsed,
    )


# ===========================================================================
# SLIM FLIPKART PROCESSING FUNCTIONS
# ===========================================================================

# ---------------------------------------------------------------------------
# FK Search Traffic Report
# ---------------------------------------------------------------------------



def _mysql_insert_fk_processed_rows(user_id, target_dates):
    """
    Build FlipkartProcessedDashboardData fully in MySQL using INSERT...SELECT.
    Two steps mirror the Amazon path:
      1. Traffic rows — LEFT JOIN PLA spend by (fsn, date).
      2. Spend-only rows — PLA rows with no matching traffic (anti-join).
    """
    traffic_table = FlipkartSearchTraffic._meta.db_table
    pla_table = FlipkartPLA._meta.db_table
    cat_table = FlipkartCategoryMap._meta.db_table
    price_table = FlipkartPrice._meta.db_table
    processed_table = FlipkartProcessedDashboardData._meta.db_table

    target_dates = sorted(target_dates or [])
    date_ph = ", ".join(["%s"] * len(target_dates)) if target_dates else ""
    date_filter = f" AND date IN ({date_ph})" if target_dates else ""

    insert_traffic_sql = f"""
        INSERT INTO {processed_table} (
            user_id, date, fsn, platform, portfolio, category, subcategory, price,
            pageviews, units, orders, revenue, total_spend, spend_sp, spend_sb, spend_sd
        )
        SELECT
            %s AS user_id,
            t.date,
            t.fsn,
            'Flipkart' AS platform,
            COALESCE(cm.portfolio, '') AS portfolio,
            COALESCE(cm.category, '') AS category,
            COALESCE(cm.subcategory, '') AS subcategory,
            COALESCE(fp.price, 0) AS price,
            COALESCE(t.pageviews, 0) AS pageviews,
            COALESCE(t.units, 0) AS units,
            0 AS orders,
            COALESCE(t.revenue, 0) AS revenue,
            COALESCE(p.total_spend, 0) AS total_spend,
            0 AS spend_sp,
            0 AS spend_sb,
            0 AS spend_sd
        FROM (
            SELECT fsn, date,
                SUM(page_views) AS pageviews,
                SUM(sales)      AS units,
                SUM(revenue)    AS revenue
            FROM {traffic_table}
            WHERE user_id = %s {date_filter}
            GROUP BY fsn, date
        ) t
        LEFT JOIN (
            SELECT fsn_id, date, SUM(ad_spend) AS total_spend
            FROM {pla_table}
            WHERE user_id = %s {date_filter}
            GROUP BY fsn_id, date
        ) p ON p.fsn_id = t.fsn AND p.date = t.date
        LEFT JOIN {cat_table} cm ON cm.user_id = %s AND cm.fsn = t.fsn
        LEFT JOIN {price_table} fp ON fp.user_id = %s AND fp.fsn = t.fsn
    """

    insert_spend_only_sql = f"""
        INSERT INTO {processed_table} (
            user_id, date, fsn, platform, portfolio, category, subcategory, price,
            pageviews, units, orders, revenue, total_spend, spend_sp, spend_sb, spend_sd
        )
        SELECT
            %s AS user_id,
            p.date,
            p.fsn_id,
            'Flipkart' AS platform,
            COALESCE(cm.portfolio, '') AS portfolio,
            COALESCE(cm.category, '') AS category,
            COALESCE(cm.subcategory, '') AS subcategory,
            COALESCE(fp.price, 0) AS price,
            0 AS pageviews,
            0 AS units,
            0 AS orders,
            0 AS revenue,
            COALESCE(p.total_spend, 0) AS total_spend,
            0 AS spend_sp,
            0 AS spend_sb,
            0 AS spend_sd
        FROM (
            SELECT fsn_id, date, SUM(ad_spend) AS total_spend
            FROM {pla_table}
            WHERE user_id = %s {date_filter}
            GROUP BY fsn_id, date
        ) p
        LEFT JOIN {cat_table} cm ON cm.user_id = %s AND cm.fsn = p.fsn_id
        LEFT JOIN {price_table} fp ON fp.user_id = %s AND fp.fsn = p.fsn_id
        WHERE NOT EXISTS (
            SELECT 1 FROM {traffic_table} t
            WHERE t.user_id = %s AND t.fsn = p.fsn_id AND t.date = p.date
        )
    """

    traffic_params = [user_id, user_id]
    if target_dates:
        traffic_params.extend(target_dates)
    traffic_params.append(user_id)
    if target_dates:
        traffic_params.extend(target_dates)
    traffic_params.extend([user_id, user_id])

    spend_only_params = [user_id, user_id]
    if target_dates:
        spend_only_params.extend(target_dates)
    spend_only_params.extend([user_id, user_id, user_id])

    rows_written = 0
    with connection.cursor() as cursor:
        cursor.execute(insert_traffic_sql, traffic_params)
        rows_written += max(int(cursor.rowcount or 0), 0)
        cursor.execute(insert_spend_only_sql, spend_only_params)
        rows_written += max(int(cursor.rowcount or 0), 0)
    return rows_written


def generate_flipkart_dashboard_data(user, progress_callback=None, only_dates=None):
    """
    Merge Flipkart reports at FSN/date level into FlipkartProcessedDashboardData.

    On MySQL the heavy lifting is done via INSERT...SELECT (same pattern as the
    Amazon path), which is 10-50× faster than Python-side streaming + bulk_create
    for large datasets.
    """

    target_dates = _parse_target_dates(only_dates)

    _t0 = time.monotonic()
    mode = f"incremental ({len(target_dates)} dates)" if target_dates else "full rebuild"

    if target_dates:
        _notify_progress(
            progress_callback, "Refreshing Flipkart dashboard aggregates for selected dates..."
        )
    else:
        _notify_progress(progress_callback, "Refreshing Flipkart dashboard aggregates...")

    processed_qs = FlipkartProcessedDashboardData.objects.filter(user=user)
    traffic_qs = FlipkartSearchTraffic.objects.filter(user=user)
    pla_qs = FlipkartPLA.objects.filter(user=user)
    if target_dates:
        processed_qs = processed_qs.filter(date__in=target_dates)
        traffic_qs = traffic_qs.filter(date__in=target_dates)
        pla_qs = pla_qs.filter(date__in=target_dates)

    # Check data availability BEFORE deleting to avoid unnecessary work.
    if not traffic_qs.exists() and not pla_qs.exists():
        processed_qs._raw_delete(processed_qs.db)
        _invalidate_user_dashboard_cache(user.id)
        logger.info("[FK Dashboard] No search traffic or PLA data - skipping.")
        return

    _t_del = time.monotonic()
    processed_qs._raw_delete(processed_qs.db)
    logger.info(
        "[FK Dashboard] delete phase user=%s mode=%s elapsed=%.1fs",
        user.id, mode, time.monotonic() - _t_del,
    )

    total_processed = 0
    _t_ins = time.monotonic()

    if connection.vendor == "mysql":
        _notify_progress(progress_callback, "Building Flipkart processed dashboard rows...")
        total_processed = _mysql_insert_fk_processed_rows(user.id, sorted(target_dates))
    else:
        # Python fallback for non-MySQL databases
        _notify_progress(progress_callback, "Loading Flipkart category and price mappings...")
        category_by_fsn = {}
        for row in FlipkartCategoryMap.objects.filter(user=user).values(
            "fsn", "portfolio", "category", "subcategory"
        ).iterator(chunk_size=DB_BATCH_SIZE):
            fsn = str(row.get("fsn") or "").strip()
            if not fsn:
                continue
            category_by_fsn[fsn] = (
                str(row.get("portfolio") or ""),
                str(row.get("category") or ""),
                str(row.get("subcategory") or ""),
            )

        price_by_fsn = {}
        for row in FlipkartPrice.objects.filter(user=user).values("fsn", "price").iterator(
            chunk_size=DB_BATCH_SIZE
        ):
            fsn = str(row.get("fsn") or "").strip()
            if not fsn:
                continue
            price_by_fsn[fsn] = _safe_float(row.get("price"))

        _notify_progress(progress_callback, "Aggregating Flipkart ad spend...")
        spend_by_key = {}
        for row in (
            pla_qs.values("fsn_id", "date")
            .annotate(total_spend=Sum("ad_spend"))
            .iterator(chunk_size=DB_BATCH_SIZE)
        ):
            date = row.get("date")
            fsn = str(row.get("fsn_id") or "").strip()
            if not date or not fsn:
                continue
            key = (date, fsn)
            spend_by_key[key] = spend_by_key.get(key, 0.0) + _safe_float(row.get("total_spend"))

        _notify_progress(progress_callback, "Building Flipkart processed dashboard rows...")
        records = []

        def _flush():
            nonlocal records, total_processed
            if not records:
                return
            FlipkartProcessedDashboardData.objects.bulk_create(records, ignore_conflicts=True)
            total_processed += len(records)
            records = []

        for row in (
            traffic_qs.values("fsn", "date")
            .annotate(page_views=Sum("page_views"), sales=Sum("sales"), revenue_total=Sum("revenue"))
            .iterator(chunk_size=DB_BATCH_SIZE)
        ):
            date = row.get("date")
            fsn = str(row.get("fsn") or "").strip()
            if not date or not fsn:
                continue
            ts = spend_by_key.pop((date, fsn), 0.0)
            portfolio, category, subcategory = category_by_fsn.get(fsn, ("", "", ""))
            records.append(FlipkartProcessedDashboardData(
                user=user, date=date, fsn=fsn, platform="Flipkart",
                portfolio=portfolio, category=category, subcategory=subcategory,
                price=_safe_float(price_by_fsn.get(fsn, 0.0)),
                pageviews=_safe_int(row.get("page_views")), units=_safe_int(row.get("sales")),
                orders=0, revenue=_safe_float(row.get("revenue_total")), total_spend=ts,
                spend_sp=0.0, spend_sb=0.0, spend_sd=0.0,
            ))
            if len(records) >= DB_BATCH_SIZE:
                _flush()

        for (date, fsn), ts in spend_by_key.items():
            portfolio, category, subcategory = category_by_fsn.get(fsn, ("", "", ""))
            records.append(FlipkartProcessedDashboardData(
                user=user, date=date, fsn=fsn, platform="Flipkart",
                portfolio=portfolio, category=category, subcategory=subcategory,
                price=_safe_float(price_by_fsn.get(fsn, 0.0)),
                pageviews=0, units=0, orders=0, revenue=0.0, total_spend=ts,
                spend_sp=0.0, spend_sb=0.0, spend_sd=0.0,
            ))
            if len(records) >= DB_BATCH_SIZE:
                _flush()
        _flush()

    logger.info(
        "[FK Dashboard] insert phase user=%s mode=%s rows=%d elapsed=%.1fs",
        user.id, mode, total_processed, time.monotonic() - _t_ins,
    )

    _invalidate_user_dashboard_cache(user.id)
    logger.info(
        "[FK Dashboard] Generated %s processed records. mode=%s elapsed=%.1fs",
        total_processed, mode, time.monotonic() - _t0,
    )
