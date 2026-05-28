import logging

from celery import shared_task
from django.conf import settings
from django.core.cache import cache

from apps.accounts.models import Users
from apps.dashboard.services.materialized_cache import cleanup_materialized_summaries
from apps.dashboard.services.warmup import prime_dashboard_payloads_for_user
from apps.dashboard.services.daily_summary import rebuild_daily_summary_for_user
from apps.dashboard.services.inventory_summary import rebuild_inventory_summary_for_user
from apps.dashboard.services.asin_monthly_summary import rebuild_asin_monthly_summary_for_user

logger = logging.getLogger(__name__)


def _inventory_summary_lock_key(data_owner_id):
    return f"dashboard_inventory_summary_task_lock_{data_owner_id}"


@shared_task
def cleanup_dashboard_materialized_summaries_task(
    retention_days=None, max_rows_per_view=None, dry_run=False
):
    stats = cleanup_materialized_summaries(
        retention_days=retention_days
        if retention_days is not None
        else getattr(settings, "DASHBOARD_SUMMARY_RETENTION_DAYS", 14),
        max_rows_per_view=max_rows_per_view
        if max_rows_per_view is not None
        else getattr(settings, "DASHBOARD_SUMMARY_MAX_ROWS_PER_VIEW", 800),
        dry_run=dry_run,
    )
    logger.info("[DashboardSummaryCleanup] %s", stats)
    return stats


@shared_task
def warmup_dashboard_payloads_task(data_owner_id, filter_sets=None, view_types=None):
    try:
        user = Users.objects.get(pk=data_owner_id)
    except Users.DoesNotExist:
        logger.warning(
            "[DashboardWarmup] Skipping warmup; user %s not found.", data_owner_id
        )
        return {"computed": 0, "error": "user-not-found"}

    max_filter_sets = getattr(settings, "DASHBOARD_WARMUP_MAX_FILTER_SETS", 7)
    stats = prime_dashboard_payloads_for_user(
        user,
        filter_sets=filter_sets,
        view_types=view_types,
        max_filter_sets=max_filter_sets,
    )
    logger.info("[DashboardWarmup] user=%s stats=%s", data_owner_id, stats)
    return stats


@shared_task
def refresh_dashboard_daily_summary_task(data_owner_id, only_dates=None):
    try:
        user = Users.objects.get(pk=data_owner_id)
    except Users.DoesNotExist:
        logger.warning(
            "[DashboardDailySummary] Skipping; user %s not found.", data_owner_id
        )
        return {"rows_written": 0, "error": "user-not-found"}

    stats = rebuild_daily_summary_for_user(user, only_dates=only_dates or [])
    logger.info("[DashboardDailySummary] user=%s stats=%s", data_owner_id, stats)
    return stats


@shared_task
def refresh_dashboard_inventory_summary_task(data_owner_id, only_dates=None):
    lock_key = _inventory_summary_lock_key(data_owner_id)
    lock_timeout = max(
        int(getattr(settings, "DASHBOARD_INVENTORY_SUMMARY_LOCK_TIMEOUT_SECONDS", 1800)),
        60,
    )
    if not cache.add(lock_key, "1", timeout=lock_timeout):
        logger.info(
            "[DashboardInventorySummary] Skipping duplicate run for user=%s",
            data_owner_id,
        )
        return {"rows_written": 0, "skipped": "duplicate-run"}

    try:
        user = Users.objects.get(pk=data_owner_id)
    except Users.DoesNotExist:
        cache.delete(lock_key)
        logger.warning(
            "[DashboardInventorySummary] Skipping; user %s not found.", data_owner_id
        )
        return {"rows_written": 0, "error": "user-not-found"}

    try:
        stats = rebuild_inventory_summary_for_user(user, only_dates=only_dates or [])
        logger.info("[DashboardInventorySummary] user=%s stats=%s", data_owner_id, stats)
        return stats
    finally:
        cache.delete(lock_key)


@shared_task
def refresh_dashboard_asin_monthly_summary_task(data_owner_id, only_months=None):
    """
    Rebuild DashboardAsinMonthlySummary for *data_owner_id*.
    only_months: optional list of "YYYY-MM-DD" strings (first day of month)
    to limit the rebuild; omit for a full rebuild.
    """
    try:
        user = Users.objects.get(pk=data_owner_id)
    except Users.DoesNotExist:
        logger.warning(
            "[DashboardAsinMonthlySummary] Skipping; user %s not found.", data_owner_id
        )
        return {"rows_written": 0, "error": "user-not-found"}

    import datetime
    parsed_months = []
    for m in (only_months or []):
        try:
            parsed_months.append(datetime.datetime.strptime(str(m)[:10], "%Y-%m-%d").date())
        except Exception:
            pass

    stats = rebuild_asin_monthly_summary_for_user(
        user, only_months=parsed_months if parsed_months else None
    )
    logger.info("[DashboardAsinMonthlySummary] user=%s stats=%s", data_owner_id, stats)
    return stats


@shared_task
def refresh_all_dashboard_daily_summaries_task():
    total_users = 0
    total_rows = 0
    for user in Users.objects.all().only("id").iterator(chunk_size=200):
        total_users += 1
        stats = rebuild_daily_summary_for_user(user, only_dates=[])
        total_rows += int(stats.get("rows_written") or 0)
    result = {"users_processed": total_users, "rows_written": total_rows}
    logger.info("[DashboardDailySummaryAll] %s", result)
    return result


@shared_task
def refresh_all_dashboard_inventory_summaries_task():
    total_users = 0
    total_rows = 0
    for user in Users.objects.all().only("id").iterator(chunk_size=200):
        total_users += 1
        stats = rebuild_inventory_summary_for_user(user, only_dates=[])
        total_rows += int(stats.get("rows_written") or 0)
    result = {"users_processed": total_users, "rows_written": total_rows}
    logger.info("[DashboardInventorySummaryAll] %s", result)
    return result
