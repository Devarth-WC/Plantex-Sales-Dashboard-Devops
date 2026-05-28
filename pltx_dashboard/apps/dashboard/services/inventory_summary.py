from django.db import transaction
from django.db.models import Sum

from apps.dashboard.models import (
    CategoryMapping,
    DashboardInventoryHealthSummary,
    FBAStockData,
    FlexStockData,
    FlipkartCategoryMap,
    FlipkartInventoryStock,
    FlipkartProcessedDashboardData,
    FlipkartSearchTraffic,
    Flipkartfba,
    ProcessedDashboardData,
)


def _safe_doc(stock_qty, sale_qty):
    if sale_qty > 0:
        return round(stock_qty / float(sale_qty), 1)
    return 999.0 if stock_qty > 0 else 0.0


def _build_amazon_rows(user, only_dates=None):
    only_dates = {str(d) for d in (only_dates or []) if str(d).strip()}

    # Pre-compute aligned dates in Python (3 cheap DISTINCT queries) to eliminate
    # the two correlated subqueries that MySQL evaluated per-row in the original SQL.
    _date_kw = {"date__in": only_dates} if only_dates else {}
    _stock_dates = (
        set(FBAStockData.objects.filter(user=user, **_date_kw).values_list("date", flat=True).distinct())
        | set(FlexStockData.objects.filter(user=user, **_date_kw).values_list("date", flat=True).distinct())
    )
    _sales_dates = set(
        ProcessedDashboardData.objects.filter(user=user, **_date_kw).values_list("date", flat=True).distinct()
    )
    aligned_dates = sorted(str(d) for d in (_stock_dates & _sales_dates))
    if not aligned_dates:
        return 0

    aligned_ph = ", ".join(["%s"] * len(aligned_dates))
    date_filter = f" AND date IN ({aligned_ph})"

    inv_table = DashboardInventoryHealthSummary._meta.db_table
    fba_table = FBAStockData._meta.db_table
    flex_table = FlexStockData._meta.db_table
    sales_table = ProcessedDashboardData._meta.db_table
    cm_table = CategoryMapping._meta.db_table

    sql = f"""
        INSERT INTO {inv_table} (
            user_id, date, platform, sku, category, portfolio, subcategory,
            status, status_class, stock_qty, fba_qty, flex_qty, sale_qty, 
            total_sales_window, drr, doc, reason, revenue
        )
        SELECT 
            %s AS user_id,
            k.date,
            'Amazon' AS platform,
            k.asin AS sku,
            COALESCE(cm.category, 'Unknown') AS category,
            COALESCE(cm.portfolio, '') AS portfolio,
            COALESCE(cm.subcategory, '') AS subcategory,
            
            CASE 
                WHEN COALESCE(fba.qty, 0) + COALESCE(flex.qty, 0) <= 0 THEN 'OOS'
                WHEN COALESCE(sales.units, 0) <= 0 THEN 'Overstock'
                WHEN (COALESCE(fba.qty, 0) + COALESCE(flex.qty, 0)) / COALESCE(sales.units, 0) <= 15 THEN 'Low Stock'
                WHEN (COALESCE(fba.qty, 0) + COALESCE(flex.qty, 0)) / COALESCE(sales.units, 0) > 60 THEN 'Overstock'
                ELSE 'In Stock'
            END AS status,
            
            CASE 
                WHEN COALESCE(fba.qty, 0) + COALESCE(flex.qty, 0) <= 0 THEN 'danger'
                WHEN COALESCE(sales.units, 0) <= 0 THEN 'neutral'
                WHEN (COALESCE(fba.qty, 0) + COALESCE(flex.qty, 0)) / COALESCE(sales.units, 0) <= 15 THEN 'warn'
                WHEN (COALESCE(fba.qty, 0) + COALESCE(flex.qty, 0)) / COALESCE(sales.units, 0) > 60 THEN 'neutral'
                ELSE 'good'
            END AS status_class,
            
            COALESCE(fba.qty, 0) + COALESCE(flex.qty, 0) AS stock_qty,
            COALESCE(fba.qty, 0) AS fba_qty,
            COALESCE(flex.qty, 0) AS flex_qty,
            COALESCE(sales.units, 0) AS sale_qty,
            COALESCE(sales.units, 0) AS total_sales_window,
            COALESCE(sales.units, 0) AS drr,
            
            CASE 
                WHEN COALESCE(sales.units, 0) > 0 THEN ROUND((COALESCE(fba.qty, 0) + COALESCE(flex.qty, 0)) / COALESCE(sales.units, 0), 1)
                WHEN COALESCE(fba.qty, 0) + COALESCE(flex.qty, 0) > 0 THEN 999.0
                ELSE 0.0
            END AS doc,
            
            CASE 
                WHEN COALESCE(fba.qty, 0) + COALESCE(flex.qty, 0) <= 0 THEN CONCAT('Stock Qty = 0 (FBA: ', COALESCE(fba.qty, 0), ', Flex: ', COALESCE(flex.qty, 0), ')')
                WHEN COALESCE(sales.units, 0) <= 0 THEN CONCAT('DOC = ∞ (Stock: ', COALESCE(fba.qty, 0) + COALESCE(flex.qty, 0), ', No sales)')
                ELSE CONCAT('DOC = ', 
                     ROUND((COALESCE(fba.qty, 0) + COALESCE(flex.qty, 0)) / COALESCE(sales.units, 0), 1),
                     ' days (Stock: ', COALESCE(fba.qty, 0) + COALESCE(flex.qty, 0), 
                     ' / Same-Day Sales: ', ROUND(COALESCE(sales.units, 0), 1), ')')
            END AS reason,
            
            COALESCE(sales.revenue, 0) AS revenue

        FROM (
            SELECT date, asin FROM {fba_table} WHERE user_id = %s {date_filter}
            UNION
            SELECT date, asin FROM {flex_table} WHERE user_id = %s {date_filter}
            UNION
            SELECT date, asin FROM {sales_table} WHERE user_id = %s {date_filter}
        ) k
        LEFT JOIN (
            SELECT date, asin, SUM(ending_warehouse_balance) as qty 
            FROM {fba_table} WHERE user_id = %s {date_filter} GROUP BY date, asin
        ) fba ON fba.date = k.date AND fba.asin = k.asin
        LEFT JOIN (
            SELECT date, asin, SUM(qty) as qty 
            FROM {flex_table} WHERE user_id = %s {date_filter} GROUP BY date, asin
        ) flex ON flex.date = k.date AND flex.asin = k.asin
        LEFT JOIN (
            SELECT date, asin, SUM(units) as units, SUM(revenue) as revenue
            FROM {sales_table} WHERE user_id = %s {date_filter} GROUP BY date, asin
        ) sales ON sales.date = k.date AND sales.asin = k.asin
        LEFT JOIN {cm_table} cm ON cm.user_id = %s AND cm.asin = k.asin
        WHERE k.date IN ({aligned_ph})
    """

    params = [user.id]
    # UNION keys
    for _ in range(3):
        params.append(user.id)
        params.extend(aligned_dates)
    # JOIN aggregations
    for _ in range(3):
        params.append(user.id)
        params.extend(aligned_dates)
    # CM join
    params.append(user.id)
    # WHERE aligned_dates
    params.extend(aligned_dates)

    from django.db import connection
    with connection.cursor() as cursor:
        cursor.execute(sql, params)
        rows_written = max(cursor.rowcount, 0)

    return rows_written


def _build_flipkart_rows(user, only_dates=None):
    only_dates = {str(d) for d in (only_dates or []) if str(d).strip()}
    traffic_qs = FlipkartSearchTraffic.objects.filter(user=user)
    inv_qs = FlipkartInventoryStock.objects.filter(user=user)
    fba_qs = Flipkartfba.objects.filter(user=user)
    rev_qs = FlipkartProcessedDashboardData.objects.filter(user=user)
    map_qs = FlipkartCategoryMap.objects.filter(user=user)

    if only_dates:
        traffic_qs = traffic_qs.filter(date__in=only_dates)
        inv_qs = inv_qs.filter(date__in=only_dates)
        fba_qs = fba_qs.filter(date__in=only_dates)
        rev_qs = rev_qs.filter(date__in=only_dates)

    sales_by_key = {}
    sales_total_by_sku = {}
    for r in traffic_qs.values("fsn", "date").annotate(s=Sum("sales")).iterator(chunk_size=5000):
        fsn = str(r["fsn"])
        sales = int(r["s"] or 0)
        sales_by_key[(fsn, r["date"])] = sales
        sales_total_by_sku[fsn] = sales_total_by_sku.get(fsn, 0) + sales
    inv_by_key = {
        (str(r["fsn"]), r["date"]): int(r["q"] or 0)
        for r in inv_qs.values("fsn", "date").annotate(q=Sum("qty"))
    }
    fba_by_key = {
        (str(r["fsn"]), r["date"]): int(r["q"] or 0)
        for r in fba_qs.values("fsn", "date").annotate(q=Sum("live_on_website"))
    }
    rev_by_key = {
        (str(r["fsn"]), r["date"]): float(r["r"] or 0)
        for r in rev_qs.values("fsn", "date").annotate(r=Sum("revenue"))
    }
    meta_by_sku = {
        str(r["fsn"]): (
            str(r.get("category") or "Unknown"),
            str(r.get("portfolio") or ""),
            str(r.get("subcategory") or ""),
        )
        for r in map_qs.values("fsn", "category", "portfolio", "subcategory")
    }

    stock_keys = set(inv_by_key.keys()) | set(fba_by_key.keys())
    sales_keys = set(sales_by_key.keys())
    stock_dates = {d for _sku, d in stock_keys if d}
    sales_dates = {d for _sku, d in sales_keys if d}
    aligned_dates = stock_dates & sales_dates
    keys = ({k for k in stock_keys if k[1] in aligned_dates} | {k for k in sales_keys if k[1] in aligned_dates}) if aligned_dates else set()

    rows = []
    for sku, row_date in keys:
        fba_qty = int(fba_by_key.get((sku, row_date), 0))
        flex_qty = int(inv_by_key.get((sku, row_date), 0))
        stock_qty = fba_qty + flex_qty
        same_day_sales = int(sales_by_key.get((sku, row_date), 0))
        total_sales = int(sales_total_by_sku.get(sku, 0))
        drr = float(total_sales) / 30.0
        if drr > 0:
            doc = round(stock_qty / drr, 1)
        else:
            doc = 999.0 if stock_qty > 0 else 0.0
        rev = float(rev_by_key.get((sku, row_date), 0.0) or 0.0)
        category, portfolio, subcategory = meta_by_sku.get(sku, ("Unknown", "", ""))

        if stock_qty <= 0:
            status = "OOS"
            status_class = "danger"
            reason = f"Stock Qty = 0 (Live on Website: {fba_qty}, FK Qty: {flex_qty})"
        elif doc < 5:
            status = "Nearly OOS"
            status_class = "danger"
            reason = f"DOC = {doc} days (Stock: {stock_qty}, DRR: {drr:.2f})"
        elif doc < 15:
            status = "Understock"
            status_class = "warn"
            reason = f"DOC = {doc} days (Stock: {stock_qty}, DRR: {drr:.2f})"
        elif doc <= 30:
            status = "Ideal Stocking"
            status_class = "good"
            reason = f"DOC = {doc} days (Stock: {stock_qty}, DRR: {drr:.2f})"
        elif doc <= 90:
            status = "Over Stock"
            status_class = "neutral"
            reason = f"DOC = {doc} days (Stock: {stock_qty}, DRR: {drr:.2f})"
        elif doc <= 180:
            status = "Highly Over Stock"
            status_class = "neutral"
            reason = f"DOC = {doc} days (Stock: {stock_qty}, DRR: {drr:.2f})"
        else:
            status = "Not Selling"
            status_class = "neutral"
            reason = f"DOC = {doc} days (Stock: {stock_qty}, DRR: {drr:.2f})"

        rows.append(
            DashboardInventoryHealthSummary(
                user=user,
                date=row_date,
                platform="Flipkart",
                sku=sku,
                category=category,
                portfolio=portfolio,
                subcategory=subcategory,
                stock_qty=stock_qty,
                fba_qty=fba_qty,
                flex_qty=flex_qty,
                sale_qty=same_day_sales,
                total_sales_window=total_sales,
                drr=round(drr, 2),
                doc=float(doc),
                revenue=round(rev, 2),
                status=status,
                status_class=status_class,
                reason=reason,
            )
        )
    return rows


def rebuild_inventory_summary_for_user(user, *, only_dates=None):
    """
    Rebuild dashboard inventory-health summary rows.
    """
    only_dates = {str(d) for d in (only_dates or []) if str(d).strip()}
    with transaction.atomic():
        scoped = DashboardInventoryHealthSummary.objects.filter(user=user)
        if only_dates:
            scoped = scoped.filter(date__in=only_dates)
        scoped.delete()

        rows_written = _build_amazon_rows(user, only_dates=only_dates)
        inserts = []
        inserts.extend(_build_flipkart_rows(user, only_dates=only_dates))
        if inserts:
            DashboardInventoryHealthSummary.objects.bulk_create(inserts, batch_size=2000)
            rows_written += len(inserts)

    return {"rows_written": rows_written, "dates_scoped": sorted(only_dates)}
