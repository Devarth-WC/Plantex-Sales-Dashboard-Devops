import pickle
import zlib
import time
import logging
from datetime import timedelta

from django.utils import timezone
from django.db.models import Count
from django.db import OperationalError

from apps.dashboard.models import DashboardMaterializedSummary

logger = logging.getLogger(__name__)


def _is_retryable_mysql_lock_error(exc):
    args = getattr(exc, "args", ()) or ()
    if not args:
        return False
    code = args[0]
    try:
        code = int(code)
    except (TypeError, ValueError):
        return False
    # 1205 = lock wait timeout, 1213 = deadlock
    return code in {1205, 1213}


def _pack_payload(payload):
    raw = pickle.dumps(payload, protocol=pickle.HIGHEST_PROTOCOL)
    return zlib.compress(raw, level=6)


def _unpack_payload(payload_blob):
    if payload_blob is None:
        return None
    return pickle.loads(zlib.decompress(bytes(payload_blob)))


def get_materialized_summary(
    *,
    user_id,
    view_type,
    data_version,
    filter_hash,
):
    row = (
        DashboardMaterializedSummary.objects.filter(
            user_id=user_id,
            view_type=view_type,
            data_version=data_version,
            filter_hash=filter_hash,
        )
        .order_by("-updated_at")
        .first()
    )
    if not row:
        return None
    return _unpack_payload(row.payload_blob)


def store_materialized_summary(
    *,
    user_id,
    view_type,
    data_version,
    filter_hash,
    normalized_filters,
    payload,
):
    payload_blob = _pack_payload(payload)
    DashboardMaterializedSummary.objects.update_or_create(
        user_id=user_id,
        view_type=view_type,
        data_version=data_version,
        filter_hash=filter_hash,
        defaults={
            "normalized_filters": normalized_filters,
            "payload_blob": payload_blob,
        },
    )


def clear_materialized_summaries_for_user(user_id):
    max_retries = 3
    for attempt in range(1, max_retries + 1):
        try:
            DashboardMaterializedSummary.objects.filter(user_id=user_id).delete()
            return True
        except OperationalError as exc:
            if not _is_retryable_mysql_lock_error(exc):
                raise
            if attempt >= max_retries:
                logger.warning(
                    "[MaterializedCache] Lock retry exhausted for user=%s while clearing summaries: %s",
                    user_id,
                    exc,
                )
                return False
            time.sleep(0.25 * attempt)


def cleanup_materialized_summaries(retention_days=14, max_rows_per_view=800, dry_run=False):
    """
    Prune old summary rows and cap table growth per (user, view_type).
    Returns operational stats.
    """
    retention_days = max(int(retention_days or 14), 1)
    max_rows_per_view = max(int(max_rows_per_view or 800), 50)

    cutoff = timezone.now() - timedelta(days=retention_days)
    old_qs = DashboardMaterializedSummary.objects.filter(updated_at__lt=cutoff)
    old_count = old_qs.count()
    deleted_old = 0
    if not dry_run and old_count:
        deleted_old, _ = old_qs.delete()

    overflow_deleted = 0
    grouped = (
        DashboardMaterializedSummary.objects.values("user_id", "view_type")
        .annotate(row_count=Count("id"))
        .filter(row_count__gt=max_rows_per_view)
    )

    for group in grouped.iterator(chunk_size=200):
        scoped_qs = DashboardMaterializedSummary.objects.filter(
            user_id=group["user_id"], view_type=group["view_type"]
        ).order_by("-updated_at", "-id")
        keep_ids = list(
            scoped_qs.values_list("id", flat=True)[:max_rows_per_view]
        )
        delete_qs = DashboardMaterializedSummary.objects.filter(
            user_id=group["user_id"], view_type=group["view_type"]
        ).exclude(id__in=keep_ids)
        if dry_run:
            overflow_deleted += delete_qs.count()
        else:
            deleted_count, _ = delete_qs.delete()
            overflow_deleted += deleted_count

    return {
        "retention_days": retention_days,
        "max_rows_per_view": max_rows_per_view,
        "old_rows_found": old_count,
        "old_rows_deleted": deleted_old if not dry_run else old_count,
        "overflow_rows_deleted": overflow_deleted,
        "dry_run": bool(dry_run),
    }
