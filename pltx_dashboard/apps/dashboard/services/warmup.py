import hashlib
import json
import logging
import time
from copy import deepcopy

from django.core.cache import cache

from apps.dashboard.models import (
    FlipkartProcessedDashboardData,
    ProcessedDashboardData,
    SpendData,
)
from apps.dashboard.services.analytics_services_orm_pipeline import (
    get_available_filters_orm_cached,
    run_orm_computation,
)
from apps.dashboard.services.cache_config import (
    DASHBOARD_CACHE_SCHEMA_VERSION,
    DASHBOARD_CACHE_TTL_LITE_SECONDS,
    DASHBOARD_PAYLOAD_CACHE_VERSION,
    DEFAULT_DASHBOARD_VIEW_TYPES,
    DEFAULT_WARMUP_FILTER_SETS,
)
from apps.dashboard.services.filters import (
    apply_dashboard_entity_filters,
    cache_filter_string,
    normalize_payload_filters,
)
from apps.dashboard.services.materialized_cache import (
    get_materialized_summary,
    store_materialized_summary,
)

logger = logging.getLogger(__name__)


def _apply_spend_filters(spend_qs, filters):
    asin_filter = filters.get("asin")
    if asin_filter:
        if isinstance(asin_filter, (list, tuple, set)):
            values = [str(v) for v in asin_filter if str(v).strip()]
            if values:
                spend_qs = spend_qs.filter(asin__in=values)
        else:
            spend_qs = spend_qs.filter(asin=asin_filter)
    return spend_qs


def _cache_key(user_id, view_type, data_version, cache_hash, section_scope="all", mode="lite"):
    return (
        f"dashboard_payload_v{DASHBOARD_PAYLOAD_CACHE_VERSION}_"
        f"s{DASHBOARD_CACHE_SCHEMA_VERSION}_"
        f"{user_id}_{view_type}_{section_scope}_{data_version}_{cache_hash}_{mode}"
    )


def prime_dashboard_payloads_for_user(
    user,
    *,
    view_types=None,
    filter_sets=None,
    data_version=None,
    max_filter_sets=None,
):
    """
    Precompute common dashboard payloads into memory cache + materialized table.
    """
    base_qs = ProcessedDashboardData.objects.filter(user=user)
    base_fk_qs = FlipkartProcessedDashboardData.objects.filter(user=user)
    if not base_qs.exists() and not base_fk_qs.exists():
        return {
            "computed": 0,
            "reused_materialized": 0,
            "reused_memory": 0,
            "skipped_no_data": 0,
            "view_types": [],
            "filters_processed": 0,
        }

    if data_version is None:
        data_version = cache.get(f"dashboard_data_version_{user.id}", 0)

    resolved_view_types = list(view_types or DEFAULT_DASHBOARD_VIEW_TYPES)
    resolved_filter_sets = [deepcopy(f) for f in (filter_sets or DEFAULT_WARMUP_FILTER_SETS)]
    if max_filter_sets is not None:
        resolved_filter_sets = resolved_filter_sets[: max(int(max_filter_sets), 0)]

    computed = 0
    reused_materialized = 0
    reused_memory = 0
    skipped_no_data = 0

    for raw_filters in resolved_filter_sets:
        filters = raw_filters or {}
        filter_key_str = cache_filter_string(filters)
        cache_hash = hashlib.md5(filter_key_str.encode("utf-8")).hexdigest()
        normalized = json.dumps(normalize_payload_filters(filters), sort_keys=True)

        platform = (filters.get("platform") or "").strip()
        show_amazon = platform != "Flipkart"
        show_flipkart = platform != "Amazon"

        cached_filter_metadata = get_available_filters_orm_cached(
            base_qs if show_amazon else base_qs.none(),
            base_fk_qs if show_flipkart else base_fk_qs.none(),
            user.id,
            show_amazon,
            show_flipkart,
        )

        scoped_qs, scoped_fk_qs = apply_dashboard_entity_filters(
            base_qs, base_fk_qs, filters
        )
        if not scoped_qs.exists() and not scoped_fk_qs.exists():
            skipped_no_data += len(resolved_view_types)
            continue

        spend_qs = _apply_spend_filters(SpendData.objects.filter(user=user), filters)

        # Check which view_types still need computation for this filter set.
        # The ORM computation result is identical for all view_types (only cache
        # key differs), so compute ONCE and reuse for all.
        views_needing_compute = []
        for view_type in resolved_view_types:
            key = _cache_key(user.id, view_type, data_version, cache_hash, section_scope="all", mode="lite")
            payload = cache.get(key)
            if payload is not None:
                reused_memory += 1
                continue

            payload = get_materialized_summary(
                user_id=user.id,
                view_type=view_type,
                data_version=data_version,
                filter_hash=cache_hash,
            )
            if payload is not None:
                cache.set(key, payload, timeout=DASHBOARD_CACHE_TTL_LITE_SECONDS)
                reused_materialized += 1
                continue

            views_needing_compute.append(view_type)

        if not views_needing_compute:
            continue

        # Compute once and cache for all view_types that need it.
        _t0 = time.monotonic()
        payload = run_orm_computation(
            scoped_qs,
            scoped_fk_qs,
            spend_qs,
            filters,
            user,
            cached_filter_metadata=cached_filter_metadata,
        )
        _elapsed = time.monotonic() - _t0
        logger.info(
            "[DashboardWarmup] Computed payload for %d view_types in %.1fs (filters=%s)",
            len(views_needing_compute), _elapsed, filter_key_str[:80],
        )
        for view_type in views_needing_compute:
            key = _cache_key(user.id, view_type, data_version, cache_hash, section_scope="all", mode="lite")
            store_materialized_summary(
                user_id=user.id,
                view_type=view_type,
                data_version=data_version,
                filter_hash=cache_hash,
                normalized_filters=normalized,
                payload=payload,
            )
            cache.set(key, payload, timeout=DASHBOARD_CACHE_TTL_LITE_SECONDS)
            computed += 1

    return {
        "computed": computed,
        "reused_materialized": reused_materialized,
        "reused_memory": reused_memory,
        "skipped_no_data": skipped_no_data,
        "view_types": resolved_view_types,
        "filters_processed": len(resolved_filter_sets),
    }
