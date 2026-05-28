from django.db import transaction, connection
from django.db.models import Sum, Value, CharField

from apps.dashboard.models import (
    DashboardDailySummary,
    ProcessedDashboardData,
    FlipkartProcessedDashboardData,
)


def rebuild_daily_summary_for_user(user, *, only_dates=None):
    """
    Rebuild day-level pre-aggregates for a user using highly optimized MySQL
    INSERT...SELECT statements, skipping Python serialization overhead.
    """
    only_dates = {str(d) for d in (only_dates or []) if str(d).strip()}

    with transaction.atomic():
        scoped = DashboardDailySummary.objects.filter(user=user)
        if only_dates:
            scoped = scoped.filter(date__in=only_dates)
        scoped.delete()

        date_filter = ""
        if only_dates:
            placeholders = ", ".join(["%s"] * len(only_dates))
            date_filter = f" AND date IN ({placeholders})"

        az_sql = f"""
            INSERT INTO {DashboardDailySummary._meta.db_table} (
                user_id, date, platform, category, portfolio, subcategory,
                revenue, orders, units, pageviews, total_spend, spend_sp, spend_sb, spend_sd
            )
            SELECT 
                user_id, date, 'Amazon', COALESCE(category, ''), COALESCE(portfolio, ''), COALESCE(subcategory, ''),
                SUM(revenue), SUM(orders), SUM(units), SUM(pageviews),
                SUM(total_spend), SUM(spend_sp), SUM(spend_sb), SUM(spend_sd)
            FROM {ProcessedDashboardData._meta.db_table}
            WHERE user_id = %s {date_filter}
            GROUP BY user_id, date, category, portfolio, subcategory
        """

        fk_sql = f"""
            INSERT INTO {DashboardDailySummary._meta.db_table} (
                user_id, date, platform, category, portfolio, subcategory,
                revenue, orders, units, pageviews, total_spend, spend_sp, spend_sb, spend_sd
            )
            SELECT 
                user_id, date, 'Flipkart', COALESCE(category, ''), COALESCE(portfolio, ''), COALESCE(subcategory, ''),
                SUM(revenue), SUM(orders), SUM(units), SUM(pageviews),
                SUM(total_spend), SUM(spend_sp), SUM(spend_sb), SUM(spend_sd)
            FROM {FlipkartProcessedDashboardData._meta.db_table}
            WHERE user_id = %s {date_filter}
            GROUP BY user_id, date, category, portfolio, subcategory
        """

        params = [user.id]
        if only_dates:
            params.extend(list(only_dates))

        rows_written = 0
        with connection.cursor() as cursor:
            cursor.execute(az_sql, params)
            rows_written += max(cursor.rowcount, 0)
            
            cursor.execute(fk_sql, params)
            rows_written += max(cursor.rowcount, 0)

    return {
        "rows_written": rows_written,
        "dates_scoped": sorted(only_dates) if only_dates else [],
    }
