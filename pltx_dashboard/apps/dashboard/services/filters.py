import json


LIST_FILTER_FIELDS = {"category", "asin", "fsn", "portfolio", "subcategory"}
PAYLOAD_FILTER_FIELDS = [
    "date_range",
    "start_date",
    "end_date",
    "compare_start_date",
    "compare_end_date",
    "platform",
    "category",
    "asin",
    "fsn",
    "portfolio",
    "subcategory",
]


def build_filters_from_querydict(querydict):
    filters = {}
    for key in querydict.keys():
        if key in LIST_FILTER_FIELDS:
            filters[key] = [value for value in querydict.getlist(key) if value]
        else:
            filters[key] = querydict.get(key, "")
    return filters


def selected_filter_payload(filters):
    return {
        "categories": filters.get("category", []),
        "asins": filters.get("asin", []),
        "fsns": filters.get("fsn", []),
    }


def _apply_value_filter(qs, field_name, value):
    if not value:
        return qs
    if isinstance(value, (list, tuple, set)):
        values = [str(item) for item in value if str(item).strip()]
        return qs.filter(**{f"{field_name}__in": values}) if values else qs
    return qs.filter(**{field_name: value})


def apply_dashboard_entity_filters(qs, fk_qs, filters):
    platform = filters.get("platform")
    show_amazon = platform != "Flipkart"
    show_flipkart = platform != "Amazon"

    qs = _apply_value_filter(qs, "category", filters.get("category"))
    fk_qs = _apply_value_filter(fk_qs, "category", filters.get("category"))

    asin_filter = filters.get("asin")
    fsn_filter = filters.get("fsn")
    qs = _apply_value_filter(qs, "asin", asin_filter)
    fk_qs = _apply_value_filter(fk_qs, "fsn", fsn_filter)

    if asin_filter and not fsn_filter:
        fk_qs = fk_qs.none()
    elif fsn_filter and not asin_filter:
        qs = qs.none()

    qs = _apply_value_filter(qs, "portfolio", filters.get("portfolio"))
    fk_qs = _apply_value_filter(fk_qs, "portfolio", filters.get("portfolio"))
    qs = _apply_value_filter(qs, "subcategory", filters.get("subcategory"))
    fk_qs = _apply_value_filter(fk_qs, "subcategory", filters.get("subcategory"))

    if not show_amazon:
        qs = qs.none()
    if not show_flipkart:
        fk_qs = fk_qs.none()

    return qs, fk_qs


def normalize_payload_filters(filters):
    cache_filters = {}
    for field in PAYLOAD_FILTER_FIELDS:
        value = filters.get(field)
        if isinstance(value, (list, tuple, set)):
            cache_filters[field] = sorted(
                {str(item) for item in value if str(item).strip()}
            )
        elif value is None:
            cache_filters[field] = ""
        else:
            cache_filters[field] = str(value)
    return cache_filters


def cache_filter_string(filters):
    return json.dumps(normalize_payload_filters(filters), sort_keys=True)
