DEFAULT_DASHBOARD_VIEW_TYPES = (
    "business-dashboard",
    "ceo-dashboard",
    "category-dashboard",
)

DASHBOARD_PAYLOAD_CACHE_VERSION = 0

# In-memory payload cache TTL tuning
DASHBOARD_CACHE_TTL_LITE_SECONDS = 60 * 60 * 6   # 6 hours
DASHBOARD_CACHE_TTL_FULL_SECONDS = 60 * 60 * 4    # 4 hours

# Cache schema marker to prevent stale-key collisions after key format changes.
DASHBOARD_CACHE_SCHEMA_VERSION = 0

DEFAULT_WARMUP_FILTER_SETS = (
    {},
    {"date_range": "last_7_days"},
    {"date_range": "last_15_days"},
    {"date_range": "last_3_months"},
    {"date_range": "last_6_months"},
    {"date_range": "last_1_year"},
    {"platform": "Amazon"},
    {"platform": "Flipkart"},
    {"platform": "Amazon", "date_range": "last_7_days"},
    {"platform": "Flipkart", "date_range": "last_7_days"},
    {"platform": "Amazon", "date_range": "last_3_months"},
    {"platform": "Flipkart", "date_range": "last_3_months"},
    {"platform": "Amazon", "date_range": "last_6_months"},
    {"platform": "Flipkart", "date_range": "last_6_months"},
    {"platform": "Amazon", "date_range": "last_1_year"},
    {"platform": "Flipkart", "date_range": "last_1_year"},
)
