import csv
import datetime
import json
import hashlib
import math
import time
from copy import deepcopy
from io import BytesIO, StringIO

import pandas as pd
from django.conf import settings
from django.core.cache import cache
from django.db.models import F, Max, Q
from django.http import FileResponse, HttpResponse, JsonResponse
from django.shortcuts import render, redirect
from django.template.loader import render_to_string
from django.utils import timezone
from django.views.decorators.http import require_GET

from apps.accounts.decorators import require_feature, _first_allowed_dashboard_for
from apps.accounts.models import Feature
from apps.accounts.utils import get_logged_in_user
from apps.dashboard.models import (
    SalesData,
    SpendData,
    ProcessedDashboardData,
    FlipkartProcessedDashboardData,
    FlipkartSearchTraffic,
    FlipkartPLA,
    DashboardInventoryHealthSummary,
    CategoryMapping,
    FlipkartCategoryMap,
)
from apps.dashboard.services.filters import (
    apply_dashboard_entity_filters,
    build_filters_from_querydict,
    cache_filter_string,
    normalize_payload_filters,
    selected_filter_payload,
)
from apps.dashboard.services.materialized_cache import (
    get_materialized_summary,
    store_materialized_summary,
)
from apps.dashboard.services.invalidation import invalidate_dashboard_cache_for_user
from apps.dashboard.services.cache_config import DASHBOARD_PAYLOAD_CACHE_VERSION
from apps.dashboard.services.cache_config import (
    DASHBOARD_CACHE_TTL_FULL_SECONDS,
    DASHBOARD_CACHE_TTL_LITE_SECONDS,
    DASHBOARD_CACHE_SCHEMA_VERSION,
)
from apps.dashboard.utils import DashboardEncoder

DASHBOARD_FEATURE_BY_VIEW = {
    "business": "business_dashboard",
    "ceo": "ceo_dashboard",
    "category": "category_dashboard",
}

DASHBOARD_SECTION_TEMPLATE_MAP = {
    ("business", "overview"): "dashboard/sections/business/overview.html",
    ("business", "visuals"): "dashboard/sections/business/visuals.html",
    ("business", "details"): "dashboard/sections/business/details.html",
    ("ceo", "overview"): "dashboard/sections/ceo/overview.html",
    ("ceo", "visuals"): "dashboard/sections/ceo/visuals.html",
    ("ceo", "details"): "dashboard/sections/ceo/details.html",
    ("category", "overview"): "dashboard/sections/category/overview.html",
    ("category", "visuals"): "dashboard/sections/category/visuals.html",
    ("category", "details"): "dashboard/sections/category/details.html",
}

DASHBOARD_MODAL_ROWS_TEMPLATE_MAP = {
    ("business", "category-growth"): ("dashboard/modals/rows/category_growth_rows.html", "category_performance"),
    ("ceo", "inventory-health"): ("dashboard/modals/rows/inventory_health_rows.html", "inventory.details"),
    ("ceo", "top-products"): ("dashboard/modals/rows/top_products_simple_rows.html", "cat_all_top_products"),
    ("ceo", "declining-products"): ("dashboard/modals/rows/declining_products_rows.html", "cat_all_under_products"),
    ("business", "inventory-health"): ("dashboard/modals/rows/inventory_health_rows.html", "inventory.details"),
    ("business", "top-products"): ("dashboard/modals/rows/top_products_simple_rows.html", "cat_all_top_products"),
    ("business", "declining-products"): ("dashboard/modals/rows/declining_products_rows.html", "cat_all_under_products"),
    ("category", "cluster-performance"): ("dashboard/modals/rows/cluster_performance_rows.html", "cluster_performance"),
    ("category", "inventory-health"): ("dashboard/modals/rows/inventory_health_rows.html", "inventory.details"),
    ("category", "top-products"): ("dashboard/modals/rows/top_products_category_rows.html", "cat_all_top_products"),
    ("category", "declining-products"): ("dashboard/modals/rows/declining_products_rows.html", "cat_all_under_products"),
}
MODAL_ROWS_DISPLAY_LIMIT = 25

DASHBOARD_PRODUCT_CARD_TEMPLATE_MAP = {
    ("business", "top-products"): "dashboard/partials/product_cards/top_products_business_rows.html",
    ("business", "declining-products"): "dashboard/partials/product_cards/declining_products_rows.html",
    ("ceo", "top-products"): "dashboard/partials/product_cards/top_products_simple_rows.html",
    ("ceo", "declining-products"): "dashboard/partials/product_cards/declining_products_rows.html",
    ("category", "top-products"): "dashboard/partials/product_cards/top_products_category_rows.html",
    ("category", "declining-products"): "dashboard/partials/product_cards/declining_products_rows.html",
}

DASHBOARD_PRODUCT_CARD_PAYLOAD_KEY_MAP = {
    "top-products": "cat_all_top_products",
    "declining-products": "cat_all_under_products",
}

DASHBOARD_CATEGORY_PERFORMANCE_ROWS_TEMPLATE_MAP = {
    "business": "dashboard/partials/category_performance_rows_business.html",
    "ceo": "dashboard/partials/category_performance_rows_ceo.html",
}


def _build_payload_json(payload):
    """
    Return full payload JSON for frontend consumers.
    """
    if not payload:
        return "null"
    return json.dumps(payload, cls=DashboardEncoder, separators=(",", ":"))


def _resolve_payload_key(payload, payload_key):
    rows = payload
    for part in str(payload_key).split("."):
        if isinstance(rows, dict):
            rows = rows.get(part)
        else:
            return []
    return rows or []


def _clean_export_value(value):
    if value is None:
        return ""
    if isinstance(value, (datetime.datetime, datetime.date)):
        return value.isoformat()
    if isinstance(value, (dict, list, tuple, set)):
        return json.dumps(value, cls=DashboardEncoder)
    return str(value)


def _rows_to_export_table(rows):
    if not rows:
        return [], []
    if isinstance(rows[0], dict):
        keys = []
        for row in rows:
            for key in row.keys():
                if key not in keys:
                    keys.append(key)
        headers = [str(k).replace("_", " ").title() for k in keys]
        table_rows = [[_clean_export_value(row.get(k)) for k in keys] for row in rows]
        return headers, table_rows
    headers = ["Value"]
    table_rows = [[_clean_export_value(r)] for r in rows]
    return headers, table_rows


def _modal_rows_export_filename(view_name, modal_key, ext):
    stamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    safe_view = str(view_name).strip().replace(" ", "_").lower()
    safe_modal = str(modal_key).strip().replace(" ", "_").replace("-", "_").lower()
    return f"{safe_view}_{safe_modal}_{stamp}.{ext}"


def _strip_non_dashboard_filters(filters):
    cleaned = dict(filters or {})
    for key in ("scope", "q", "page", "page_size", "export"):
        cleaned.pop(key, None)
    return cleaned


def _list_or_scalar_filter(qs, field_name, value):
    if not value:
        return qs
    if isinstance(value, (list, tuple, set)):
        values = [str(item) for item in value if str(item).strip()]
        return qs.filter(**{f"{field_name}__in": values}) if values else qs
    return qs.filter(**{field_name: value})


def _modal_row_text(row):
    if isinstance(row, dict):
        return " ".join(_clean_export_value(value).lower() for value in row.values())
    return _clean_export_value(row).lower()


def _filter_rows_by_query(rows, query):
    if not query:
        return rows
    needle = str(query).strip().lower()
    if not needle:
        return rows
    return [row for row in rows if needle in _modal_row_text(row)]


def _paginate_rows(rows, page, page_size):
    total = len(rows)
    start = (page - 1) * page_size
    return total, rows[start : start + page_size]


def _get_filtered_processed_querysets(data_owner, filters):
    qs = ProcessedDashboardData.objects.filter(user=data_owner)
    fk_qs = FlipkartProcessedDashboardData.objects.filter(user=data_owner)
    return apply_dashboard_entity_filters(qs, fk_qs, filters)


def _get_light_filter_metadata(data_owner_id, data_version):
    """
    Main dashboard payloads do not need full dropdown option lists.
    Remote Select2 endpoints provide paginated options on demand.
    """
    cache_key = f"dashboard_light_filter_metadata_{data_owner_id}_{data_version}"
    metadata = cache.get(cache_key)
    if metadata:
        return metadata

    platforms = []
    if ProcessedDashboardData.objects.filter(user_id=data_owner_id).exists():
        platforms.append("Amazon")
    if FlipkartProcessedDashboardData.objects.filter(user_id=data_owner_id).exists():
        platforms.append("Flipkart")

    metadata = {
        "asins": [],
        "fsns": [],
        "categories": [],
        "portfolios": [],
        "subcategories": [],
        "platforms": platforms,
        "dates": [],
    }
    cache.set(cache_key, metadata, timeout=3600)
    return metadata


def _get_top_product_modal_rows(data_owner, filters):
    from apps.dashboard.services.analytics_services_orm_pipeline import (
        apply_global_filters_orm,
        _build_top_product_rows,
        get_prev_period_qs,
    )

    qs, fk_qs = _get_filtered_processed_querysets(data_owner, filters)
    qs_f = apply_global_filters_orm(qs, filters)
    fk_qs_f = apply_global_filters_orm(fk_qs, filters)
    qs_prev = get_prev_period_qs(qs, filters)
    fk_prev = get_prev_period_qs(fk_qs, filters)
    asin_meta = {
        row["asin"]: {"portfolio": row["portfolio"] or ""}
        for row in CategoryMapping.objects.filter(user=data_owner).values("asin", "portfolio")
    }
    fsn_meta = {
        row["fsn"]: {"portfolio": row["portfolio"] or ""}
        for row in FlipkartCategoryMap.objects.filter(user=data_owner).values("fsn", "portfolio")
    }

    return _build_top_product_rows(
        qs_f,
        fk_qs_f,
        qs_prev,
        fk_prev,
        asin_meta=asin_meta,
        fsn_meta=fsn_meta,
        include_full_payload=True,
    )


def _get_declining_product_modal_rows(data_owner, filters):
    from apps.dashboard.services.analytics_services_orm_pipeline import (
        _build_declining_product_rows,
        resolve_growth_period,
        safe_shift_month,
    )

    qs, fk_qs = _get_filtered_processed_querysets(data_owner, filters)
    max_az = qs.aggregate(m=Max("date"))
    max_fk = fk_qs.aggregate(m=Max("date"))
    latest_dates = [item.get("m") for item in (max_az, max_fk) if item.get("m")]
    has_explicit_period = bool(
        filters.get("date_range") or filters.get("start_date") or filters.get("end_date")
    )
    if has_explicit_period:
        reference_date = timezone.localdate()
    else:
        reference_date = max(latest_dates) if latest_dates else datetime.date.today()
    cm_start, cm_end = resolve_growth_period(filters, reference_date)
    pm_start = safe_shift_month(cm_start, -1)
    pm_end = safe_shift_month(cm_end, -1)
    period_start = min(cm_start, pm_start)
    period_end = max(cm_end, pm_end)

    return _build_declining_product_rows(
        qs,
        fk_qs,
        cm_start,
        cm_end,
        pm_start,
        pm_end,
        include_full_payload=True,
    )


def _inventory_summary_platform(filters):
    platform = (filters.get("platform") or "").strip()
    asin_filter = filters.get("asin")
    fsn_filter = filters.get("fsn")
    if platform == "Flipkart" or (fsn_filter and not asin_filter):
        return "Flipkart"
    return "Amazon"


def _get_inventory_modal_queryset(data_owner, filters, query):
    from apps.dashboard.services.analytics_services_orm_pipeline import (
        apply_global_filters_orm,
    )

    platform = _inventory_summary_platform(filters)
    qs = DashboardInventoryHealthSummary.objects.filter(
        user=data_owner,
        platform=platform,
    )
    qs = apply_global_filters_orm(qs, filters)
    qs = _list_or_scalar_filter(qs, "category", filters.get("category"))
    qs = _list_or_scalar_filter(qs, "portfolio", filters.get("portfolio"))
    qs = _list_or_scalar_filter(qs, "subcategory", filters.get("subcategory"))

    sku_filter = filters.get("fsn") if platform == "Flipkart" else filters.get("asin")
    qs = _list_or_scalar_filter(qs, "sku", sku_filter)

    if query:
        needle = str(query).strip()
        qs = qs.filter(
            Q(sku__icontains=needle)
            | Q(category__icontains=needle)
            | Q(portfolio__icontains=needle)
            | Q(subcategory__icontains=needle)
            | Q(status__icontains=needle)
            | Q(reason__icontains=needle)
        )
    return qs.order_by("-date", "-revenue", "sku")


def _inventory_summary_row_dict(row):
    return {
        "date": row.date,
        "sku": row.sku,
        "category": row.category or "Unknown",
        "stock_qty": int(row.stock_qty or 0),
        "fba_qty": int(row.fba_qty or 0),
        "flex_qty": int(row.flex_qty or 0),
        "sale_qty": int(row.sale_qty or 0),
        "total_sales_30d": int(row.total_sales_window or 0),
        "drr": round(float(row.drr or 0), 2),
        "doc": float(row.doc or 0),
        "units": int(row.sale_qty or 0),
        "revenue": round(float(row.revenue or 0), 2),
        "status": row.status,
        "status_class": row.status_class,
        "reason": row.reason,
    }


def _build_template_payload(payload):
    """
    Keep template payload separate from cached payload mutation.
    """
    return deepcopy(payload) if isinstance(payload, dict) else payload


def _trim_payload_for_initial_load(payload):
    """
    Keep initial section payload lightweight; large modal datasets are loaded on demand.
    """
    if not isinstance(payload, dict):
        return payload
    payload = deepcopy(payload)
    payload["cat_all_top_products"] = []
    payload["cat_all_under_products"] = []
    if isinstance(payload.get("category_performance"), list):
        payload["category_performance"] = payload["category_performance"][:25]
    if isinstance(payload.get("cluster_performance"), list):
        payload["cluster_performance"] = payload["cluster_performance"][:25]
    forecast = payload.get("forecast")
    if isinstance(forecast, dict) and isinstance(forecast.get("details"), list):
        forecast["details"] = forecast["details"][:31]
    inventory = payload.get("inventory")
    if isinstance(inventory, dict):
        inventory["details"] = []
        inventory["details_shown"] = 0
        inventory["details_truncated"] = False
    return payload


def _get_dashboard_refresh_status(data_owner_id):
    cache_key = f"dashboard_refresh_status_{data_owner_id}"
    status = cache.get(cache_key)
    if not isinstance(status, dict):
        return {"state": "idle", "message": ""}
    state = str(status.get("state") or "idle").lower()
    if state not in {"idle", "processing", "success", "error"}:
        state = "idle"
    message = str(status.get("message") or "")
    now = time.time()

    if state == "processing":
        # Guard against stale "processing" banners when no real work remains.
        # Three independent signals indicate genuine activity:
        #   1. A refresh lock held by an active Celery task
        #   2. A recent cache "ping" from _set_dashboard_refresh_status (≤120s)
        #   3. Recently-updated UploadLog entries in QUEUED/PROCESSING state (≤30m)
        # If ALL three are absent/stale, the banner is a leftover from a
        # crashed/killed worker and should be reset to idle.

        # -- Signal 1: Refresh lock --
        lock_key = f"dashboard_refresh_lock_{data_owner_id}"
        lock_ts_key = f"{lock_key}_ts"
        has_refresh_lock = bool(cache.get(lock_key))

        # Detect stale locks from crashed workers. The lock itself has a
        # 1800s Redis TTL, but the worker may have died without cleanup.
        # If the lock's timestamp is missing or older than 15 minutes,
        # treat it as abandoned and clear it.
        if has_refresh_lock:
            lock_ts = cache.get(lock_ts_key)
            lock_is_stale = True
            if lock_ts:
                try:
                    lock_age = now - float(lock_ts)
                    lock_is_stale = lock_age > 900  # 15 minutes
                except (ValueError, TypeError):
                    lock_is_stale = True
            if lock_is_stale:
                cache.delete(lock_key)
                cache.delete(lock_ts_key)
                has_refresh_lock = False

        # -- Signal 2: Recent processing ping --
        has_recent_processing_ping = False
        ts = status.get("updated_at_ts")
        if isinstance(ts, (int, float)):
            has_recent_processing_ping = (now - float(ts)) <= 120

        # -- Signal 3: Active UploadLog entries --
        # Only hit the DB if Signals 1 and 2 haven't already confirmed activity.
        # Results are cached 15s to absorb the 3s polling cadence.
        has_active_upload_logs = False
        if not (has_refresh_lock or has_recent_processing_ping):
            _ul_key = f"dashboard_upload_log_active_{data_owner_id}"
            _ul_cached = cache.get(_ul_key)
            if _ul_cached is None:
                try:
                    from apps.upload.models import UploadLog
                    stale_cutoff = datetime.datetime.now() - datetime.timedelta(minutes=30)
                    has_active_upload_logs = UploadLog.objects.filter(
                        data_owner_id=data_owner_id,
                        status__in=[
                            UploadLog.STATUS_QUEUED,
                            UploadLog.STATUS_PROCESSING,
                        ],
                        updated_at__gte=stale_cutoff,
                    ).exists()
                    cache.set(_ul_key, has_active_upload_logs, timeout=15)
                    # Auto-cleanup: throttled to once per 60s to avoid write storms.
                    _cleanup_key = f"dashboard_upload_log_cleanup_{data_owner_id}"
                    if not cache.get(_cleanup_key):
                        cache.set(_cleanup_key, 1, timeout=60)
                        UploadLog.objects.filter(
                            data_owner_id=data_owner_id,
                            status__in=[
                                UploadLog.STATUS_QUEUED,
                                UploadLog.STATUS_PROCESSING,
                            ],
                            updated_at__lt=stale_cutoff,
                        ).update(
                            status=UploadLog.STATUS_ERROR,
                            message="Automatically marked as failed — task did not complete within 30 minutes.",
                        )
                except Exception:
                    has_active_upload_logs = False
            else:
                has_active_upload_logs = _ul_cached

        if not (has_refresh_lock or has_active_upload_logs or has_recent_processing_ping):
            cache.set(
                cache_key,
                {"state": "idle", "message": "", "updated_at_ts": now},
                timeout=300,
            )
            return {"state": "idle", "message": ""}

    # Prevent stale terminal banners from persisting across page refreshes.
    # Keep only recent success/error updates visible.
    if state in {"success", "error"}:
        ts = status.get("updated_at_ts")
        # Backward compatibility: older cache entries without timestamp are stale.
        is_stale = (not isinstance(ts, (int, float))) or ((now - float(ts)) > 45)
        if is_stale:
            cache.set(
                cache_key,
                {"state": "idle", "message": "", "updated_at_ts": now},
                timeout=300,
            )
            return {"state": "idle", "message": ""}
    return {"state": state, "message": message}


def _payload_needs_refresh(payload):
    """
    Detect stale cached payloads from older schema versions that can
    cause oversized HTML responses and outdated calculations.
    """
    if not isinstance(payload, dict):
        return True

    inventory = payload.get("inventory")
    if not isinstance(inventory, dict):
        return True

    required_inventory_keys = {
        "details_total",
        "details_shown",
        "details_truncated",
        "has_stock_data",
        "num_sale_days",
    }
    if not required_inventory_keys.issubset(inventory.keys()):
        return True

    return False


def _is_kpis_only_payload(payload):
    """
    Detect KPI-only payloads so analytics sections do not reuse them from
    materialized summaries.
    """
    if not isinstance(payload, dict):
        return True
    scope = str(payload.get("_compute_scope") or "").lower()
    if scope == "kpis":
        return True
    if scope == "full":
        return False
    # Backward-compatible heuristic for pre-marker payloads.
    charts = payload.get("charts")
    if isinstance(charts, dict) and not charts:
        forecast = payload.get("forecast") or {}
        if not payload.get("category_performance") and not payload.get("cluster_performance"):
            if int(forecast.get("days_in_month") or 0) == 0:
                return True
    return False


def no_cache_for_htmx(view_func):
    """Decorator to prevent caching of HTMX requests"""

    def wrapper(request, *args, **kwargs):
        response = view_func(request, *args, **kwargs)

        # Set no-cache headers for HTMX requests
        if request.headers.get("HX-Request") == "true":
            response["Cache-Control"] = "no-cache, no-store, must-revalidate, max-age=0"
            response["Pragma"] = "no-cache"
            response["Expires"] = "0"

        return response

    return wrapper


def dashboard_view(request):
    # Redirect the user to the first dashboard they have access to.
    user = get_logged_in_user(request)
    if not user:
        return redirect("account-login")
    route = _first_allowed_dashboard_for(user)
    return redirect(route)


def _ensure_processed_tables_if_missing(data_owner):
    """
    Self-heal when processed dashboard tables are empty but raw upload tables exist.
    This guards against edge cases where upload completion succeeded but the final
    processed-table build was skipped/interrupted.
    """
    refresh_status = _get_dashboard_refresh_status(data_owner.id)
    if refresh_status.get("state") == "processing":
        return

    presence_key = f"processed_tables_present_{data_owner.id}"
    if cache.get(presence_key):
        return

    has_amz_processed = ProcessedDashboardData.objects.filter(user=data_owner).exists()
    has_fk_processed = FlipkartProcessedDashboardData.objects.filter(user=data_owner).exists()

    if has_amz_processed or has_fk_processed:
        cache.set(presence_key, True, timeout=300)
        return

    has_amz_raw = (
        SalesData.objects.filter(user=data_owner).exists()
        or SpendData.objects.filter(user=data_owner).exists()
    )
    has_fk_raw = (
        FlipkartSearchTraffic.objects.filter(user=data_owner).exists()
        or FlipkartPLA.objects.filter(user=data_owner).exists()
    )

    if not has_amz_raw and not has_fk_raw:
        return

    from apps.upload.dashboard_builders import (
        generate_dashboard_data,
        generate_flipkart_dashboard_data,
    )

    if has_amz_raw:
        generate_dashboard_data(data_owner)
    if has_fk_raw:
        generate_flipkart_dashboard_data(data_owner)


def get_dashboard_context(
    request,
    include_payload=True,
    cache_view_type=None,
    include_full_payload=False,
    section_scope="all",
    compute_scope="full",
):
    user = get_logged_in_user(request)
    if not user:
        return None

    data_owner = user.created_by if user.created_by else user

    if user.is_main_user:
        _feat_key = "all_feature_codenames_v1"
        user_features = cache.get(_feat_key)
        if user_features is None:
            user_features = list(Feature.objects.values_list("code_name", flat=True))
            cache.set(_feat_key, user_features, timeout=3600)
    else:
        if user.role:
            _feat_key = f"role_feature_codenames_v1_{user.role_id}"
            user_features = cache.get(_feat_key)
            if user_features is None:
                user_features = list(
                    user.role.features.values_list("code_name", flat=True)
                )
                cache.set(_feat_key, user_features, timeout=3600)
        else:
            user_features = []

    filters = build_filters_from_querydict(request.GET)
    filters.pop("scope", None)
    selected_filters = selected_filter_payload(filters)

    if not include_payload:
        refresh_status = _get_dashboard_refresh_status(data_owner.id)
        return {
            "logged_user": user,
            "user_features": user_features,
            "payload": None,
            "payload_json": "null",
            "filters": filters,
            "selected_filters": selected_filters,
            "selected_filters_json": json.dumps(selected_filters),
            "dashboard_refresh_status": refresh_status,
            "dashboard_refresh_status_json": json.dumps(refresh_status),
        }

    _ensure_processed_tables_if_missing(data_owner)
    qs = ProcessedDashboardData.objects.filter(user=data_owner)
    fk_qs = FlipkartProcessedDashboardData.objects.filter(user=data_owner)

    data_version = cache.get(f"dashboard_data_version_{data_owner.id}", 0)
    cached_filter_metadata = _get_light_filter_metadata(data_owner.id, data_version)
    filter_key_str = cache_filter_string(filters)
    cache_hash = hashlib.md5(filter_key_str.encode("utf-8")).hexdigest()

    qs, fk_qs = apply_dashboard_entity_filters(qs, fk_qs, filters)

    presence_cache_key = (
        f"dashboard_presence_v1_{data_owner.id}_{data_version}_{cache_hash}"
    )
    has_filtered_rows = cache.get(presence_cache_key)
    if has_filtered_rows is None:
        has_filtered_rows = qs.exists() or fk_qs.exists()
        cache.set(presence_cache_key, has_filtered_rows, timeout=300)

    if not has_filtered_rows:
        refresh_status = _get_dashboard_refresh_status(data_owner.id)
        return {
            "logged_user": user,
            "user_features": user_features,
            "payload": None,
            "payload_json": "null",
            "filters": filters,
            "selected_filters": selected_filters,
            "selected_filters_json": json.dumps(selected_filters),
            "dashboard_refresh_status": refresh_status,
            "dashboard_refresh_status_json": json.dumps(refresh_status),
        }

    # Apply same entity filters to spend data at DB level
    spend_qs = SpendData.objects.filter(user=data_owner)
    asin_filter = filters.get("asin")
    if asin_filter:
        if isinstance(asin_filter, (list, tuple)):
            spend_qs = spend_qs.filter(asin__in=asin_filter)
        else:
            spend_qs = spend_qs.filter(asin=asin_filter)

    from apps.dashboard.services.analytics_services_orm_pipeline import run_orm_computation

    # Normalize filters once; reuse in memory cache + materialized summary table.
    view_type = cache_view_type or request.resolver_match.url_name or "shared"

    cache_mode = "full" if include_full_payload else "lite"
    cache_key = (
        f"dashboard_payload_v{DASHBOARD_PAYLOAD_CACHE_VERSION}_"
        f"s{DASHBOARD_CACHE_SCHEMA_VERSION}_"
        f"{data_owner.id}_{view_type}_{section_scope}_{data_version}_{cache_hash}_{cache_mode}"
    )

    payload = cache.get(cache_key)
    if payload and _payload_needs_refresh(payload):
        payload = None

    if not payload and not include_full_payload:
        payload = get_materialized_summary(
            user_id=data_owner.id,
            view_type=view_type,
            data_version=data_version,
            filter_hash=cache_hash,
        )
        if payload and _payload_needs_refresh(payload):
            payload = None
        elif str(compute_scope or "full").lower() == "full" and _is_kpis_only_payload(payload):
            payload = None

    if not payload:
        # For full-scope sections (visuals/details) share the lock across sections so
        # parallel page loads don't run the same heavy queries twice simultaneously.
        is_shared_full_lock = not include_full_payload and str(compute_scope or "full").lower() == "full"
        if is_shared_full_lock:
            compute_lock_key = (
                f"dashboard_compute_lock_v{DASHBOARD_PAYLOAD_CACHE_VERSION}_"
                f"{data_owner.id}_{view_type}_{data_version}_{cache_hash}"
            )
        else:
            compute_lock_key = f"{cache_key}:lock"
        have_lock = cache.add(compute_lock_key, "1", timeout=120)
        if not have_lock:
            # Another section is computing the same dataset; wait and reuse.
            # With monthly summary the typical computation is 1–3 s, so reduce
            # wait_sleep to 0.5 s and check materialized summary every 4 iterations
            # instead of every iteration (reduces repeated DB hits from ~30 to ~8).
            wait_iters, wait_sleep = (24, 0.5) if is_shared_full_lock else (8, 0.15)
            for _wi, _ in enumerate(range(wait_iters)):
                time.sleep(wait_sleep)
                payload = cache.get(cache_key)
                if payload:
                    break
                # Check shared materialized summary every 4 iterations (every 2 s)
                # to avoid N×DashboardMaterializedSummary queries per waiting section.
                if not payload and is_shared_full_lock and (_wi % 4 == 0):
                    payload = get_materialized_summary(
                        user_id=data_owner.id,
                        view_type=view_type,
                        data_version=data_version,
                        filter_hash=cache_hash,
                    )
                    if payload and _payload_needs_refresh(payload):
                        payload = None
                    elif payload and _is_kpis_only_payload(payload):
                        payload = None
                if payload:
                    break
        if not payload:
            try:
                payload = run_orm_computation(
                    qs,
                    fk_qs,
                    spend_qs,
                    filters,
                    data_owner,
                    cached_filter_metadata=cached_filter_metadata,
                    include_full_payload=include_full_payload,
                    compute_scope=compute_scope,
                    cache_identity={
                        "data_version": data_version,
                        "filter_hash": cache_hash,
                    },
                    section_scope=section_scope,
                    dashboard_view=(request.resolver_match.kwargs.get("view_name") if request.resolver_match else None),
                )
                if (not include_full_payload) and str(compute_scope or "full").lower() == "full":
                    try:
                        store_materialized_summary(
                            user_id=data_owner.id,
                            view_type=view_type,
                            data_version=data_version,
                            filter_hash=cache_hash,
                            normalized_filters=json.dumps(
                                normalize_payload_filters(filters), sort_keys=True
                            ),
                            payload=payload,
                        )
                    except Exception:
                        # Materialized summaries are a performance layer; do not fail requests.
                        pass

                cache.set(
                    cache_key,
                    payload,
                    timeout=(
                        DASHBOARD_CACHE_TTL_FULL_SECONDS
                        if include_full_payload
                        else DASHBOARD_CACHE_TTL_LITE_SECONDS
                    ),
                )
            finally:
                if have_lock:
                    cache.delete(compute_lock_key)

    if not include_full_payload:
        payload = _trim_payload_for_initial_load(payload)

    template_payload = _build_template_payload(payload)
    refresh_status = _get_dashboard_refresh_status(data_owner.id)

    return {
        "logged_user": user,
        "user_features": user_features,
        "payload": template_payload,
        "payload_json": _build_payload_json(payload),
        "filters": filters,
        "selected_filters": selected_filters,
        "selected_filters_json": json.dumps(selected_filters),
        "dashboard_refresh_status": refresh_status,
        "dashboard_refresh_status_json": json.dumps(refresh_status),
    }


def _inject_htmx(request, ctx):
    """
    Inject base_template into context.
    Ensures base_template is ALWAYS set to prevent extends tag errors.
    """
    # Ensure ctx is always a dict (not None)
    if ctx is None:
        ctx = {
            "logged_user": None,
            "user_features": [],
            "payload": None,
            "payload_json": "null",
            "filters": {},
            "selected_filters": {},
            "selected_filters_json": "{}",
        }

    # Determine which base template to use
    is_htmx_request = request.headers.get("HX-Request") == "true"
    ctx["base_template"] = (
        "dashboard/base_htmx.html"
        if is_htmx_request
        else "dashboard/base_dashboard.html"
    )

    return ctx


# ─────────────────────────────────────────────────────────
# Dashboard views
# ─────────────────────────────────────────────────────────


@require_feature("business_dashboard")
@no_cache_for_htmx
def business_dashboard_view(request):
    ctx = get_dashboard_context(
        request,
        include_payload=False,
        cache_view_type="business-dashboard",
    )
    if ctx is None:
        return redirect("account-login")
    return render(
        request, "dashboard/business_dashboard.html", _inject_htmx(request, ctx)
    )


@require_feature("ceo_dashboard")
@no_cache_for_htmx
def ceo_dashboard_view(request):
    ctx = get_dashboard_context(
        request,
        include_payload=False,
        cache_view_type="ceo-dashboard",
    )
    if ctx is None:
        return redirect("account-login")
    return render(request, "dashboard/ceo_dashboard.html", _inject_htmx(request, ctx))


@require_feature("category_dashboard")
@no_cache_for_htmx
def category_dashboard_view(request):
    ctx = get_dashboard_context(
        request,
        include_payload=False,
        cache_view_type="category-dashboard",
    )
    if ctx is None:
        return redirect("account-login")
    return render(
        request, "dashboard/category_dashboard.html", _inject_htmx(request, ctx)
    )


def _user_has_feature(user, feature_code):
    if user.is_main_user:
        return True
    return bool(
        user.role and user.role.features.filter(code_name=feature_code).exists()
    )


@no_cache_for_htmx
def dashboard_section_view(request, view_name, section):
    user = get_logged_in_user(request)
    if not user:
        return JsonResponse({"error": "Not authenticated"}, status=401)

    feature_code = DASHBOARD_FEATURE_BY_VIEW.get(view_name)
    if not feature_code:
        return JsonResponse({"error": "Invalid dashboard view."}, status=404)
    if not _user_has_feature(user, feature_code):
        return JsonResponse({"error": "Permission denied."}, status=403)

    template_name = DASHBOARD_SECTION_TEMPLATE_MAP.get((view_name, section))
    if not template_name:
        return JsonResponse({"error": "Invalid section."}, status=404)

    ctx = get_dashboard_context(
        request,
        include_payload=True,
        cache_view_type=f"{view_name}-dashboard",
        section_scope=section,
        compute_scope="kpis" if section == "overview" else "full",
    )
    if ctx is None:
        return JsonResponse({"error": "Not authenticated"}, status=401)

    ctx["dashboard_section_view_type"] = view_name
    ctx["dashboard_section_name"] = section
    ctx["section_template"] = template_name
    return render(request, "dashboard/sections/section_wrapper.html", ctx)


@no_cache_for_htmx
def dashboard_modal_rows_view(request, view_name, modal_key):
    user = get_logged_in_user(request)
    if not user:
        return JsonResponse({"error": "Not authenticated"}, status=401)

    feature_code = DASHBOARD_FEATURE_BY_VIEW.get(view_name)
    if not feature_code:
        return JsonResponse({"error": "Invalid dashboard view."}, status=404)
    if not _user_has_feature(user, feature_code):
        return JsonResponse({"error": "Permission denied."}, status=403)

    modal_tpl = DASHBOARD_MODAL_ROWS_TEMPLATE_MAP.get((view_name, modal_key))
    if not modal_tpl:
        return JsonResponse({"error": "Invalid modal key."}, status=404)
    template_name, payload_key = modal_tpl

    export_format = (request.GET.get("export") or "").strip().lower()
    query = (request.GET.get("q") or "").strip().lower()
    page = _parse_positive_int(request.GET.get("page"), default=1, minimum=1, maximum=10_000)
    page_size = _parse_positive_int(
        request.GET.get("page_size"), default=50, minimum=10, maximum=200
    )

    data_owner = user.created_by if user.created_by else user
    data_version = cache.get(f"dashboard_data_version_{data_owner.id}", 0)
    filters = _strip_non_dashboard_filters(build_filters_from_querydict(request.GET))
    filter_hash = hashlib.md5(cache_filter_string(filters).encode("utf-8")).hexdigest()

    modal_rows_cache_key = (
        "dashboard_modal_rows_v2_"
        f"{data_owner.id}_{view_name}_{modal_key}_{data_version}_{filter_hash}_"
        f"{hashlib.md5(query.encode('utf-8')).hexdigest()}_{page}_{page_size}"
    )
    modal_rows_cache_ttl = int(
        getattr(settings, "DASHBOARD_MODAL_ROWS_CACHE_TTL_SECONDS", 180)
    )

    # DataTables mode: client handles all pagination/search — return all rows at once.
    load_all = request.GET.get("all") == "1" and modal_key != "inventory-health"

    if export_format not in {"csv", "excel", "xlsx"} and not load_all:
        cached_modal_payload = cache.get(modal_rows_cache_key)
        if cached_modal_payload:
            return JsonResponse(cached_modal_payload)

    # All-rows cache: keyed without page/page_size so page 2+ hits this and paginates in Python.
    all_rows_cache_key = (
        "dashboard_modal_all_rows_v3_"
        f"{data_owner.id}_{view_name}_{modal_key}_{data_version}_{filter_hash}_"
        f"{hashlib.md5(query.encode('utf-8')).hexdigest()}"
    )

    total = 0
    rows = None

    if modal_key == "inventory-health":
        inventory_qs = _get_inventory_modal_queryset(data_owner, filters, query)
        total = inventory_qs.count()
        row_qs = (
            inventory_qs
            if export_format in {"csv", "excel", "xlsx"}
            else inventory_qs[(page - 1) * page_size : page * page_size]
        )
        rows = [_inventory_summary_row_dict(row) for row in row_qs]
    else:
        # Fast path 1: per-page HTML already cached (only for non-DataTables mode)
        # (handled above via modal_rows_cache_key check)

        # Fast path 2: full rows already cached in memory
        if not export_format:
            rows = cache.get(all_rows_cache_key)

        # Fast path 3: reuse materialized summary computed by the section view.
        # Only for modals whose payload key is always fully populated in the materialized
        # payload — top-products and declining-products store empty lists there.
        MATERIALIZED_MODAL_KEYS = {"cluster-performance", "category-growth"}
        if rows is None and modal_key in MATERIALIZED_MODAL_KEYS:
            mat_payload = get_materialized_summary(
                user_id=data_owner.id,
                view_type=f"{view_name}-dashboard",
                data_version=data_version,
                filter_hash=filter_hash,
            )
            if mat_payload and not _payload_needs_refresh(mat_payload):
                mat_rows = _resolve_payload_key(mat_payload, payload_key)
                if isinstance(mat_rows, list) and mat_rows:
                    rows = _filter_rows_by_query(mat_rows, query)
                    if not export_format:
                        cache.set(all_rows_cache_key, rows, timeout=modal_rows_cache_ttl)

        # Slow path: compute from scratch
        if rows is None:
            if modal_key == "top-products":
                rows = _filter_rows_by_query(_get_top_product_modal_rows(data_owner, filters), query)
            elif modal_key == "declining-products":
                rows = _filter_rows_by_query(
                    _get_declining_product_modal_rows(data_owner, filters), query
                )
            else:
                ctx = get_dashboard_context(
                    request,
                    include_payload=True,
                    cache_view_type=f"{view_name}-dashboard",
                    section_scope="analytics",
                    compute_scope="full",
                )
                if ctx is None:
                    return JsonResponse({"error": "Not authenticated"}, status=401)
                payload = ctx.get("payload") or {}
                rows = _resolve_payload_key(payload, payload_key)
                if not isinstance(rows, list):
                    rows = []
                rows = _filter_rows_by_query(rows, query)

            if not export_format:
                cache.set(all_rows_cache_key, rows or [], timeout=modal_rows_cache_ttl)

        if rows is None:
            rows = []
        total = len(rows)

    if export_format in {"csv", "excel", "xlsx"}:
        headers, table_rows = _rows_to_export_table(rows)
        if export_format == "csv":
            output = StringIO()
            writer = csv.writer(output)
            writer.writerow(headers)
            writer.writerows(table_rows)
            buf = BytesIO(output.getvalue().encode("utf-8"))
            response = FileResponse(buf, content_type="text/csv")
            response["Content-Disposition"] = (
                f'attachment; filename="{_modal_rows_export_filename(view_name, modal_key, "csv")}"'
            )
            return response

        buf = BytesIO()
        pd.DataFrame(table_rows, columns=headers).to_excel(buf, index=False)
        buf.seek(0)
        response = FileResponse(
            buf,
            content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )
        response["Content-Disposition"] = (
            f'attachment; filename="{_modal_rows_export_filename(view_name, modal_key, "xlsx")}"'
        )
        return response

    if modal_key == "inventory-health":
        rows_total, page_rows = total, rows
        total_pages = math.ceil(rows_total / page_size) if rows_total > 0 else 0
    elif load_all:
        # DataTables client-side: send all rows at once, no server pagination.
        page_rows = rows
        rows_total = total
        total_pages = 1
    else:
        rows_total, page_rows = _paginate_rows(rows, page, page_size)
        total_pages = math.ceil(rows_total / page_size) if rows_total > 0 else 0

    html = render_to_string(
        template_name,
        {
            "rows": page_rows,
            "rows_total": rows_total,
            "rows_shown": len(page_rows),
            "rows_truncated": len(page_rows) < rows_total,
        },
        request=request,
    )
    payload = {
        "html": html,
        "use_datatable": load_all,
        "pagination": {
            "page": page,
            "page_size": page_size,
            "total": rows_total,
            "total_pages": total_pages,
            "has_prev": page > 1,
            "has_next": page < total_pages,
        },
    }
    if not load_all:
        cache.set(modal_rows_cache_key, payload, timeout=modal_rows_cache_ttl)
    return JsonResponse(payload)


@no_cache_for_htmx
def dashboard_product_card_rows_view(request, view_name, card_key):
    user = get_logged_in_user(request)
    if not user:
        return JsonResponse({"error": "Not authenticated"}, status=401)

    feature_code = DASHBOARD_FEATURE_BY_VIEW.get(view_name)
    if not feature_code:
        return JsonResponse({"error": "Invalid dashboard view."}, status=404)
    if not _user_has_feature(user, feature_code):
        return JsonResponse({"error": "Permission denied."}, status=403)

    template_name = DASHBOARD_PRODUCT_CARD_TEMPLATE_MAP.get((view_name, card_key))
    if not template_name:
        return JsonResponse({"error": "Invalid product card."}, status=404)
    payload_key = DASHBOARD_PRODUCT_CARD_PAYLOAD_KEY_MAP.get(card_key)
    if not payload_key:
        return JsonResponse({"error": "Invalid product card."}, status=404)

    data_owner = user.created_by if user.created_by else user
    data_version = cache.get(f"dashboard_data_version_{data_owner.id}", 0)
    filters = _strip_non_dashboard_filters(build_filters_from_querydict(request.GET))
    filter_hash = hashlib.md5(cache_filter_string(filters).encode("utf-8")).hexdigest()
    cache_key = (
        "dashboard_product_card_rows_v1_"
        f"{data_owner.id}_{view_name}_{card_key}_{data_version}_{filter_hash}"
    )

    cached_html = cache.get(cache_key)
    if cached_html:
        return HttpResponse(cached_html)

    ctx = get_dashboard_context(
        request,
        include_payload=True,
        cache_view_type=f"{view_name}-dashboard",
        section_scope="analytics",
        compute_scope="full",
    )
    if ctx is None:
        return JsonResponse({"error": "Not authenticated"}, status=401)

    rows = _resolve_payload_key((ctx.get("payload") or {}), payload_key)
    if not isinstance(rows, list):
        rows = []
    rows = rows[:5]

    html = render_to_string(template_name, {"rows": rows}, request=request)
    cache.set(cache_key, html, timeout=300)
    return HttpResponse(html)


@no_cache_for_htmx
def dashboard_category_performance_rows_view(request, view_name):
    user = get_logged_in_user(request)
    if not user:
        return JsonResponse({"error": "Not authenticated"}, status=401)

    feature_code = DASHBOARD_FEATURE_BY_VIEW.get(view_name)
    if not feature_code:
        return JsonResponse({"error": "Invalid dashboard view."}, status=404)
    if not _user_has_feature(user, feature_code):
        return JsonResponse({"error": "Permission denied."}, status=403)

    template_name = DASHBOARD_CATEGORY_PERFORMANCE_ROWS_TEMPLATE_MAP.get(view_name)
    if not template_name:
        return JsonResponse({"error": "Invalid dashboard view."}, status=404)

    page = _parse_positive_int(request.GET.get("page"), default=1, minimum=1, maximum=10_000)
    page_size = _parse_positive_int(
        request.GET.get("page_size"), default=10, minimum=1, maximum=50
    )
    query = (request.GET.get("q") or "").strip().lower()

    ctx = get_dashboard_context(
        request,
        include_payload=True,
        cache_view_type=f"{view_name}-dashboard",
        section_scope="analytics",
        compute_scope="full",
    )
    if ctx is None:
        return JsonResponse({"error": "Not authenticated"}, status=401)

    rows = (ctx.get("payload") or {}).get("category_performance") or []
    if query:
        rows = [r for r in rows if query in str(r.get("category", "")).lower()]

    total = len(rows)
    start = (page - 1) * page_size
    page_rows = rows[start : start + page_size]
    total_pages = math.ceil(total / page_size) if total > 0 else 0

    html = render_to_string(
        template_name,
        {
            "rows": page_rows,
            "start_index": start,
        },
        request=request,
    )
    return JsonResponse(
        {
            "html": html,
            "pagination": {
                "page": page,
                "page_size": page_size,
                "total": total,
                "total_pages": total_pages,
                "has_prev": page > 1,
                "has_next": page < total_pages,
            },
        }
    )


@require_feature("upload_data")
def upload_view(request):
    user = get_logged_in_user(request)
    if not user:
        return redirect("account-login")
    data_owner = user.created_by if user.created_by else user

    if user.is_main_user:
        _feat_key = "all_feature_codenames_v1"
        user_features = cache.get(_feat_key)
        if user_features is None:
            user_features = list(Feature.objects.values_list("code_name", flat=True))
            cache.set(_feat_key, user_features, timeout=3600)
    else:
        if user.role:
            _feat_key = f"role_feature_codenames_v1_{user.role_id}"
            user_features = cache.get(_feat_key)
            if user_features is None:
                user_features = list(
                    user.role.features.values_list("code_name", flat=True)
                )
                cache.set(_feat_key, user_features, timeout=3600)
        else:
            user_features = []
    from apps.upload.models import UploadLog
    upload_logs = UploadLog.objects.filter(data_owner=data_owner).select_related(
        "uploaded_by"
    )[:100]

    upload_task_timeout_seconds = int(
        getattr(settings, "UPLOAD_TASK_TIMEOUT_SECONDS", 1800)
    )

    return render(
        request,
        "dashboard/upload.html",
        {
            "logged_user": user,
            "user_features": user_features,
            "upload_logs": upload_logs,
            "payload_json": "null",
            "selected_filters_json": "{}",
            "dashboard_refresh_status_json": '{"state":"idle","message":""}',
            "upload_task_timeout_ms": max(upload_task_timeout_seconds, 60) * 1000,
        },
    )


def dashboard_refresh_status(request):
    user = get_logged_in_user(request)
    if not user:
        return JsonResponse({"error": "Not authenticated"}, status=401)
    data_owner = user.created_by if user.created_by else user
    return JsonResponse(_get_dashboard_refresh_status(data_owner.id))


@require_GET
def dashboard_refresh_now(request):
    user = get_logged_in_user(request)
    if not user:
        return JsonResponse({"error": "Not authenticated"}, status=401)

    data_owner = user.created_by if user.created_by else user
    invalidate_dashboard_cache_for_user(data_owner.id, clear_materialized=True)

    response = JsonResponse(
        {
            "ok": True,
            "message": "Dashboard cache cleared. Reloading fresh data.",
        }
    )
    response["Cache-Control"] = "no-cache, no-store, must-revalidate, max-age=0"
    response["Pragma"] = "no-cache"
    response["Expires"] = "0"
    return response


def _parse_positive_int(value, default, minimum=1, maximum=200):
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return default
    if parsed < minimum:
        return minimum
    if parsed > maximum:
        return maximum
    return parsed


def _distinct_option_values_qs(qs, field_name):
    return (
        qs.annotate(option_value=F(field_name))
        .values("option_value")
        .distinct()
    )


def _search_paginated_single_source(base_qs, field_name, q, offset, page_size):
    values_qs = _distinct_option_values_qs(base_qs, field_name)
    if not q:
        ordered = values_qs.order_by("option_value")
        total = ordered.count()
        rows = list(ordered[offset : offset + page_size])
        return total, [str(r["option_value"]).strip() for r in rows if r["option_value"]]

    starts_qs = values_qs.filter(**{f"{field_name}__istartswith": q}).order_by(
        "option_value"
    )
    contains_qs = (
        values_qs.filter(**{f"{field_name}__icontains": q})
        .exclude(**{f"{field_name}__istartswith": q})
        .order_by("option_value")
    )
    starts_total = starts_qs.count()
    contains_total = contains_qs.count()
    total = starts_total + contains_total

    if offset < starts_total:
        rows = list(starts_qs[offset : offset + page_size])
        remaining = page_size - len(rows)
        if remaining > 0:
            rows.extend(list(contains_qs[:remaining]))
    else:
        contains_offset = offset - starts_total
        rows = list(contains_qs[contains_offset : contains_offset + page_size])

    return total, [str(r["option_value"]).strip() for r in rows if r["option_value"]]


def _search_paginated_dual_source(az_qs, fk_qs, field_name, q, offset, page_size):
    az_values = _distinct_option_values_qs(az_qs, field_name)
    fk_values = _distinct_option_values_qs(fk_qs, field_name)
    if not q:
        merged_qs = az_values.union(fk_values).order_by("option_value")
        total = merged_qs.count()
        rows = list(merged_qs[offset : offset + page_size])
        return total, [str(r["option_value"]).strip() for r in rows if r["option_value"]]

    az_starts = az_values.filter(**{f"{field_name}__istartswith": q})
    fk_starts = fk_values.filter(**{f"{field_name}__istartswith": q})
    starts_qs = az_starts.union(fk_starts).order_by("option_value")

    az_contains = az_values.filter(**{f"{field_name}__icontains": q}).exclude(
        **{f"{field_name}__istartswith": q}
    )
    fk_contains = fk_values.filter(**{f"{field_name}__icontains": q}).exclude(
        **{f"{field_name}__istartswith": q}
    )
    contains_qs = az_contains.union(fk_contains).order_by("option_value")

    starts_total = starts_qs.count()
    contains_total = contains_qs.count()
    total = starts_total + contains_total

    if offset < starts_total:
        rows = list(starts_qs[offset : offset + page_size])
        remaining = page_size - len(rows)
        if remaining > 0:
            rows.extend(list(contains_qs[:remaining]))
    else:
        contains_offset = offset - starts_total
        rows = list(contains_qs[contains_offset : contains_offset + page_size])

    return total, [str(r["option_value"]).strip() for r in rows if r["option_value"]]


def filter_dropdown_options(request):
    """
    Paginated + search-backed filter option endpoint.
    Uses the currently applied dashboard filters (except the requested field)
    so dropdown options remain context-aware.
    """
    user = get_logged_in_user(request)
    if not user:
        return JsonResponse({"error": "Not authenticated"}, status=401)
    if not user.is_main_user:
        if not user.role:
            return JsonResponse({"error": "Permission denied."}, status=403)
        has_dashboard_feature = user.role.features.filter(
            code_name__in={"business_dashboard", "ceo_dashboard", "category_dashboard"}
        ).exists()
        if not has_dashboard_feature:
            return JsonResponse({"error": "Permission denied."}, status=403)

    data_owner = user.created_by if user.created_by else user
    field = (request.GET.get("field") or "").strip().lower()
    if field not in {"category", "asin", "fsn", "portfolio", "subcategory"}:
        return JsonResponse({"error": "Invalid field."}, status=400)

    q = (request.GET.get("q") or "").strip()
    page = _parse_positive_int(request.GET.get("page"), default=1, minimum=1, maximum=10_000)
    page_size = _parse_positive_int(
        request.GET.get("page_size"), default=50, minimum=10, maximum=100
    )
    offset = (page - 1) * page_size

    filters = build_filters_from_querydict(request.GET)
    filters.pop("field", None)
    filters.pop("q", None)
    filters.pop("page", None)
    filters.pop("page_size", None)
    # Don't self-filter the requested dropdown field.
    filters.pop(field, None)

    data_version = cache.get(f"dashboard_data_version_{data_owner.id}", 0)
    dropdown_cache_hash = hashlib.md5(
        cache_filter_string(filters).encode("utf-8")
    ).hexdigest()
    dropdown_cache_key = (
        f"dashboard_filter_options_v2_{data_owner.id}_{data_version}_{field}_"
        f"{hashlib.md5(q.lower().encode('utf-8')).hexdigest()}_{page}_{page_size}_{dropdown_cache_hash}"
    )
    cached_payload = cache.get(dropdown_cache_key)
    if cached_payload:
        return JsonResponse(cached_payload)

    qs = ProcessedDashboardData.objects.filter(user=data_owner)
    fk_qs = FlipkartProcessedDashboardData.objects.filter(user=data_owner)
    qs, fk_qs = apply_dashboard_entity_filters(qs, fk_qs, filters)

    results = []
    total = 0

    if field == "asin":
        asin_qs = qs.exclude(asin__isnull=True).exclude(asin="")
        total, results = _search_paginated_single_source(
            asin_qs, "asin", q, offset, page_size
        )
    elif field == "fsn":
        fsn_qs = fk_qs.exclude(fsn__isnull=True).exclude(fsn="")
        total, results = _search_paginated_single_source(
            fsn_qs, "fsn", q, offset, page_size
        )
    else:
        az_qs = qs.exclude(**{f"{field}__isnull": True}).exclude(**{f"{field}": ""})
        fk_field_qs = fk_qs.exclude(**{f"{field}__isnull": True}).exclude(
            **{f"{field}": ""}
        )
        total, results = _search_paginated_dual_source(
            az_qs, fk_field_qs, field, q, offset, page_size
        )
    payload = {
        "field": field,
        "results": [{"value": value, "label": value} for value in results],
        "pagination": {
            "page": page,
            "page_size": page_size,
            "total": total,
            "has_next": offset + page_size < total,
        },
    }
    cache.set(dropdown_cache_key, payload, timeout=300)
    return JsonResponse(payload)


def _demo_specs(today):
    day_ddmmyyyy = today.strftime("%d-%m-%Y")
    day_ymd = today.strftime("%Y-%m-%d")
    return {
        # Upload Data (Amazon)
        "upload_sales": {
            "kind": "csv",
            "filename": f"{day_ddmmyyyy}.csv",
            "columns": [
                "(Child) ASIN",
                "Page Views - Total",
                "Units Ordered",
                "Ordered Product Sales",
                "Total Order Items",
            ],
            "rows": [["B0DEMOASIN1", 245, 18, "₹25,499.00", 17]],
        },
        "upload_category": {
            "kind": "csv",
            "filename": "category_mapping_demo.csv",
            "columns": ["ASIN", "Portfolio", "Category", "Subcategory", "Skus"],
            "rows": [["B0DEMOASIN1", "Home", "Storage", "Bins", "SKU-DEMO-1"]],
        },
        "upload_spend": {
            "kind": "csv",
            "filename": "ads_spend_demo.csv",
            "columns": ["Date", "Ad Account", "Ad Type", "ASIN", "Spend"],
            "rows": [[day_ymd, "Main Ads", "SP", "B0DEMOASIN1", 1250.50]],
        },
        "upload_price": {
            "kind": "csv",
            "filename": "pricing_data_demo.csv",
            "columns": ["ASIN", "Price"],
            "rows": [["B0DEMOASIN1", 1499]],
        },
        "upload_fba_stock": {
            "kind": "csv",
            "filename": "fba_stock_demo.csv",
            "columns": [
                "Date",
                "FNSKU",
                "ASIN",
                "MSKU",
                "Title",
                "Disposition",
                "Starting Warehouse Balance",
                "In Transit Between Warehouses",
                "Receipts",
                "Customer Shipments",
                "Customer Returns",
                "Vendor Returns",
                "Warehouse Transfer In/Out",
                "Found",
                "Lost",
                "Damaged",
                "Disposed",
                "Other Events",
                "Ending Warehouse Balance",
                "Unknown Events",
                "Location",
            ],
            "rows": [
                [
                    day_ymd,
                    "X000DEMOFNSKU",
                    "B0DEMOASIN1",
                    "MSKU-DEMO-1",
                    "Demo Product",
                    "SELLABLE",
                    120,
                    10,
                    15,
                    8,
                    0,
                    0,
                    0,
                    0,
                    0,
                    0,
                    0,
                    0,
                    137,
                    0,
                    "DEL4",
                ]
            ],
        },
        "upload_flex_stock": {
            "kind": "csv",
            "filename": "flex_stock_demo.csv",
            "columns": ["Date", "ASIN", "Cluster", "Qty"],
            "rows": [[day_ymd, "B0DEMOASIN1", "BANGALORE", 42]],
        },
        # Upload Data (Flipkart)
        "fk_search_traffic": {
            "kind": "csv",
            "filename": "fk_search_traffic_demo.csv",
            "columns": [
                "Listing Id",
                "SKU Id",
                "Vertical",
                "Impression Date",
                "Product Clicks",
                "Sales",
                "Revenue",
            ],
            "rows": [["ABCDEMOFSN00000001X", "FK-SKU-1", "Home Furnishing", day_ymd, 320, 22, 28765]],
        },
        "fk_category": {
            "kind": "csv",
            "filename": "fk_category_demo.csv",
            "columns": [
                "FSN ID",
                "SKU",
                "Portfolio",
                "Category",
                "Sub Category",
                "Vertical",
                "Product Status",
            ],
            "rows": [
                [
                    "DEMOFSN00000001",
                    "FK-SKU-1",
                    "Home",
                    "Storage",
                    "Bins",
                    "Home Furnishing",
                    "Continue",
                ]
            ],
        },
        "fk_price": {
            "kind": "csv",
            "filename": "fk_price_demo.csv",
            "columns": ["Flipkart Serial Number", "Deal"],
            "rows": [["DEMOFSN00000001", 1349]],
        },
        "fk_pla": {
            "kind": "csv_with_metadata",
            "filename": "fk_pla_demo.csv",
            "metadata_rows": [["Start Time,2026-01-01 00:00:00"], ["End Time,2026-01-01 23:59:59"]],
            "columns": ["Campaign ID", "Advertised FSN ID", "Ad Spend"],
            "rows": [["CMP-1001", "DEMOFSN00000001", 842.75]],
        },
        "fk_fba_stock": {
            "kind": "csv",
            "filename": "fk_fba_stock_demo.csv",
            "columns": [
                "Date",
                "Warehouse Id",
                "SKU",
                "Title",
                "Listing Id",
                "FSN",
                "Brand",
                "Flipkart Selling Price",
                "Live on Website",
            ],
            "rows": [
                [
                    day_ymd,
                    "blr_main_wh",
                    "FK-SKU-1",
                    "Demo FK Product",
                    "LSTDEMOFSN00000001XYZ",
                    "DEMOFSN00000001",
                    "Plantex",
                    1349,
                    87,
                ]
            ],
        },
        "fk_inventory": {
            "kind": "xlsx",
            "filename": "fk_inventory_demo.xlsx",
            "columns": ["PRODUCTS STATUS", "PRODUCTS TYPE", "SKU", "FSN", "Qty"],
            "rows": [["Continued", "Storage", "FK-SKU-1", "DEMOFSN00000001", 42]],
        },
        # Replenishment
        "repl_sales": {
            "kind": "csv",
            "filename": "replenishment_sales_demo.csv",
            "columns": [
                "FC CODE",
                "Shipment To Postal Code",
                "ASIN",
                "Customer Shipment Date",
                "Quantity",
                "Amazon Order ID",
                "Product Amount",
                "Shipping Amount",
                "Gift Amount",
            ],
            "rows": [["DEL4", "560001", "B0DEMOASIN1", f"{day_ymd}T10:10:00+05:30", 2, "AMZ-ORD-1001", 1499, 40, 0]],
        },
        "repl_stock": {
            "kind": "xlsx",
            "filename": "replenishment_stock_demo.xlsx",
            "columns": [
                "ASIN",
                "Location",
                "Disposition",
                "Ending Warehouse Balance",
                "In Transit Between Warehouses",
            ],
            "rows": [["B0DEMOASIN1", "DEL4", "sellable", 150, 12]],
        },
        "repl_lis": {
            "kind": "xlsx",
            "filename": "replenishment_lis_demo.xlsx",
            "columns": ["ASIN", "Cluster", "Sum of Local Shipped Units", "Sum of Total Units"],
            "rows": [["B0DEMOASIN1", "BANGALORE", 18, 30]],
        },
        "repl_shipment": {
            "kind": "xlsx",
            "filename": "replenishment_shipment_demo.xlsx",
            "columns": [
                "ASIN",
                "CLUSTER",
                "FC",
                "STATUS",
                "FINAL QTY",
                "ID",
                "APPOINTMENT DATE",
                "LOADING DATE",
            ],
            "rows": [["B0DEMOASIN1", "BANGALORE", "DEL4", "Upcoming", 45, "SHP-1001", day_ymd, day_ymd]],
        },
        "repl_assortment": {
            "kind": "xlsx",
            "filename": "replenishment_assortment_demo.xlsx",
            "columns": [
                "ASIN",
                "SKU",
                "HSN CODE",
                "VENDOR NAME",
                "PRODUCTS STATUS",
                "ACT WEIGHT",
                "VOLUMETRIC WEIGHT",
                "PRODUCT TYPE",
                "PRODUCT SIZE",
                "Portfolio",
                "Category",
                "Brand",
            ],
            "rows": [["B0DEMOASIN1", "SKU-DEMO-1", "392490", "Demo Vendor", "Active", 0.75, 1.10, "Storage", "Medium", "Home", "Bins", "Plantex"]],
        },
        "repl_fc_cluster": {
            "kind": "xlsx",
            "filename": "replenishment_fc_cluster_demo.xlsx",
            "columns": ["FC CODE", "FC TYPE", "CLUSTER NAME", "ZONE"],
            "rows": [["DEL4", "AMAZON", "DELHI", "North"]],
        },
        "repl_pincode_cluster": {
            "kind": "csv",
            "filename": "replenishment_pincode_cluster_demo.csv",
            "columns": ["PIN CODE", "Fulfilment Cluster", "IDEAL CLUSTER", "ZONE"],
            "rows": [["560001", "BANGALORE", "BLR_CLUSTER", "South"]],
        },
        "repl_input_sheet": {
            "kind": "xlsx",
            "filename": "replenishment_input_sheet_demo.xlsx",
            "columns": ["Particular", "Value"],
            "rows": [
                ["P0 Demand DOC", "15 Days"],
                ["P1 Demand DOC", "30 Days"],
                ["P2 Demand DOC", "60 Days"],
                ["Sale Report Days", "7 Days"],
                ["Stock Report Date", day_ymd],
            ],
        },
        "repl_business_report": {
            "kind": "csv",
            "filename": "replenishment_business_report_demo.csv",
            "columns": [
                "(Child) ASIN",
                "Page Views - Total",
                "Units Ordered",
                "Ordered Product Sales",
                "Total Order Items",
            ],
            "rows": [["B0DEMOASIN1", 180, 14, "₹19,999.00", 13]],
        },
        "repl_flex_qty": {
            "kind": "csv",
            "filename": "replenishment_flex_qty_demo.csv",
            "columns": ["ASIN", "Cluster", "Qty"],
            "rows": [["B0DEMOASIN1", "BANGALORE", 20]],
        },
    }


def _table_to_dataframe(columns, rows):
    return pd.DataFrame(rows, columns=columns)


def download_demo_template(request):
    user = get_logged_in_user(request)
    if not user:
        return redirect("account-login")

    template_key = (request.GET.get("template") or "").strip()
    specs = _demo_specs(datetime.date.today())
    spec = specs.get(template_key)
    if not spec:
        return JsonResponse(
            {"error": "Invalid template key. Please provide a valid template."},
            status=400,
        )

    kind = spec["kind"]
    filename = spec["filename"]

    if kind in {"csv", "csv_with_metadata"}:
        output = StringIO()
        if kind == "csv_with_metadata":
            for row in spec.get("metadata_rows", []):
                output.write(",".join(str(v) for v in row) + "\n")
        writer = csv.writer(output)
        writer.writerow(spec["columns"])
        writer.writerows(spec["rows"])
        data = output.getvalue().encode("utf-8")
        buf = BytesIO(data)
        response = FileResponse(buf, content_type="text/csv")
    elif kind == "xlsx":
        buf = BytesIO()
        df = _table_to_dataframe(spec["columns"], spec["rows"])
        with pd.ExcelWriter(buf, engine="openpyxl") as writer:
            df.to_excel(writer, index=False, sheet_name="Sheet1")
        buf.seek(0)
        response = FileResponse(
            buf,
            content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )
    elif kind == "xlsx_multi":
        buf = BytesIO()
        with pd.ExcelWriter(buf, engine="openpyxl") as writer:
            for sheet_name, sheet in spec["sheets"].items():
                df = _table_to_dataframe(sheet["columns"], sheet["rows"])
                df.to_excel(writer, index=False, sheet_name=sheet_name)
        buf.seek(0)
        response = FileResponse(
            buf,
            content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )
    else:
        return JsonResponse({"error": "Unsupported template type."}, status=400)

    response["Content-Disposition"] = f'attachment; filename="{filename}"'
    return response


def download_calculated_data(request, file_format):
    """Download the calculated/merged dashboard data as CSV or Excel.

    Uses the same filters currently applied on the dashboard.
    The export mirrors the logic from scripts/cleaning_mapping_merging.py.
    """
    from apps.dashboard.services.export_services import export_csv, export_excel
    from datetime import datetime

    user = get_logged_in_user(request)
    if not user:
        return redirect("account-login")

    filters = build_filters_from_querydict(request.GET)

    # Optional export override:
    # If dashboard platform filter is "All", frontend can pass export_platform=Amazon|Flipkart
    # to force a platform-specific export schema/calculation set.
    export_platform = (request.GET.get("export_platform") or "").strip()
    if export_platform in {"Amazon", "Flipkart"}:
        filters["platform"] = export_platform

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")

    if file_format == "csv":
        buf = export_csv(user, filters)
        response = FileResponse(buf, content_type="text/csv")
        response["Content-Disposition"] = (
            f'attachment; filename="Calculated_Dashboard_Data_{timestamp}.csv"'
        )
        return response
    elif file_format == "excel":
        buf = export_excel(user, filters)
        response = FileResponse(
            buf,
            content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )
        response["Content-Disposition"] = (
            f'attachment; filename="Calculated_Dashboard_Data_{timestamp}.xlsx"'
        )
        return response
    else:
        from django.http import JsonResponse

        return JsonResponse(
            {"error": "Invalid format. Use 'csv' or 'excel'."}, status=400
        )
