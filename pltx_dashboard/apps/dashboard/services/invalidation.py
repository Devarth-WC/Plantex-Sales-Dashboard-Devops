from django.core.cache import cache
import logging

from apps.dashboard.services.materialized_cache import clear_materialized_summaries_for_user

logger = logging.getLogger(__name__)

def _bump_dashboard_data_version(user_id):
    key = f"dashboard_data_version_{user_id}"
    try:
        cache.add(key, 0, timeout=None)
        cache.incr(key)
    except Exception:
        current = cache.get(key, 0) or 0
        cache.set(key, int(current) + 1, timeout=None)


def invalidate_dashboard_cache_for_user(user_id, *, clear_materialized=True):
    """
    Invalidate dashboard payload/filter caches for a specific data owner.
    This is safe to call from both web and Celery processes.
    """
    _bump_dashboard_data_version(user_id)

    for amz in (True, False):
        for flp in (True, False):
            cache.delete(f"dashboard_filters_{user_id}_{amz}_{flp}")

    # Bust the per-user category mapping caches so the next request reloads
    # fresh data after an upload changes CategoryMapping / FlipkartCategoryMap.
    cache.delete(f"asin_meta_v1_{user_id}")
    cache.delete(f"fsn_meta_v1_{user_id}")

    if clear_materialized:
        try:
            clear_materialized_summaries_for_user(user_id)
        except Exception:
            # Invalidation should never break upload/dashboard jobs.
            logger.exception(
                "[DashboardInvalidation] Failed clearing materialized summaries for user=%s",
                user_id,
            )
