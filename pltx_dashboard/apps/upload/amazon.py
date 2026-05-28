import logging

from apps.dashboard.models import (
    CategoryMapping,
    FBAStockData,
    FlexStockData,
    PriceData,
    SalesData,
    SpendData,
)
from apps.dashboard.utils import clean_currency, clean_number
from apps.upload.parsers import iter_file_chunks, parse_report_date
from apps.upload.schema import parse_sales_upload_date, require_columns

from .service_common import DB_BATCH_SIZE, get_upsert_kwargs

logger = logging.getLogger(__name__)


def process_category_file(file_obj, user):
    """
    Upsert category mappings scoped to the given user.
    - Uses bulk_create with update_conflicts to elegantly update existing records.
    """
    any_chunk = False
    touched_asins = set()
    for df in iter_file_chunks(file_obj):
        any_chunk = True
        require_columns(df, "category")

        new_mappings = []
        for row in df.to_dict("records"):
            asin = str(row.get("ASIN", "")).strip()
            if not asin or asin.lower() == "nan":
                continue
            touched_asins.add(asin)

            new_mappings.append(
                CategoryMapping(
                    user=user,
                    asin=asin,
                    portfolio=str(row.get("Portfolio", "")).strip(),
                    category=str(row.get("Category", "")).strip(),
                    subcategory=str(row.get("Subcategory", "")).strip(),
                )
            )

        if new_mappings:
            for i in range(0, len(new_mappings), DB_BATCH_SIZE):
                CategoryMapping.objects.bulk_create(
                    new_mappings[i : i + DB_BATCH_SIZE],
                    **get_upsert_kwargs(
                        unique_fields=["user", "asin"],
                        update_fields=["portfolio", "category", "subcategory"],
                    ),
                )

    if not any_chunk:
        raise ValueError("Category Mapping file is empty.")
    return touched_asins


# ---------------------------------------------------------------------------
# Price
# ---------------------------------------------------------------------------


def process_price_file(file_obj, user):
    """
    Upsert price data scoped to the given user.
    - Uses bulk_create with update_conflicts to smartly update existing values.
    """
    any_chunk = False
    touched_asins = set()
    for df in iter_file_chunks(file_obj):
        any_chunk = True
        require_columns(df, "price")

        new_prices = []
        for row in df.to_dict("records"):
            asin = str(row.get("ASIN", "")).strip()
            if not asin or asin.lower() == "nan":
                continue
            touched_asins.add(asin)
            new_prices.append(
                PriceData(user=user, asin=asin, price=clean_currency(row.get("Price", 0)))
            )

        if new_prices:
            for i in range(0, len(new_prices), DB_BATCH_SIZE):
                PriceData.objects.bulk_create(
                    new_prices[i : i + DB_BATCH_SIZE],
                    **get_upsert_kwargs(
                        unique_fields=["user", "asin"], update_fields=["price"]
                    ),
                )

    if not any_chunk:
        raise ValueError("Pricing Data file is empty.")
    return touched_asins


# ---------------------------------------------------------------------------
# Spend
# ---------------------------------------------------------------------------


def process_spend_file(file_obj, user):
    """
    Insert spend rows scoped to the user, or update existing records directly if they already exist.

    Spend values are pre-aggregated (summed) per unique key
    (user, date, asin, ad_account, ad_type) before upserting, so duplicate
    rows in the source file are combined instead of silently overwritten.
    """
    total_spends = 0
    touched_dates = set()
    any_chunk = False
    row_number = 1
    for df in iter_file_chunks(file_obj):
        any_chunk = True
        require_columns(df, "spend")

        # Pre-aggregate spend by unique key to avoid losing data when the
        # source file contains duplicate (date, asin, ad_account, ad_type) rows.
        spend_agg = {}  # key -> summed spend
        for row in df.to_dict("records"):
            row_number += 1
            asin = str(row.get("ASIN", "")).strip()
            if not asin or asin.lower() == "nan":
                continue

            try:
                row_date = parse_report_date(row.get("Date"), prefer_dayfirst=False)
            except Exception as exc:
                raise ValueError(
                    f"Invalid Date value in Ads Spends at row {row_number}: {exc}"
                )
            touched_dates.add(row_date)

            ad_type = str(row.get("Ad Type", "")).strip().upper()
            if ad_type in ("SPONSORED PRODUCTS", "SP"):
                ad_type = "SP"
            elif ad_type in ("SPONSORED BRANDS", "SB"):
                ad_type = "SB"
            elif ad_type in ("SPONSORED DISPLAY", "SD"):
                ad_type = "SD"
            else:
                ad_type = ad_type[:10]

            ad_account = str(row.get("Ad Account", "")).strip()
            spend_val = clean_currency(row.get("Spend", 0))

            key = (row_date, asin, ad_account, ad_type)
            spend_agg[key] = spend_agg.get(key, 0.0) + spend_val

        # Build SpendData objects from the aggregated dict
        new_spends = []
        for (row_date, asin, ad_account, ad_type), spend_total in spend_agg.items():
            new_spends.append(
                SpendData(
                    user=user,
                    date=row_date,
                    asin=asin,
                    ad_account=ad_account,
                    ad_type=ad_type,
                    spend=spend_total,
                )
            )

        total_spends += len(new_spends)
        if new_spends:
            for i in range(0, len(new_spends), DB_BATCH_SIZE):
                SpendData.objects.bulk_create(
                    new_spends[i : i + DB_BATCH_SIZE],
                    **get_upsert_kwargs(
                        unique_fields=["user", "date", "asin", "ad_account", "ad_type"],
                        update_fields=["spend"],
                    ),
                )

    if not any_chunk:
        raise ValueError("Ads Spends file is empty.")

    logger.info("[SpendData] Processed and upserted %s records.", total_spends)
    return touched_dates


# ---------------------------------------------------------------------------
# Sales
# ---------------------------------------------------------------------------


def process_sales_file(file_obj, date_str, user):
    """
    Insert sales rows scoped to the user, or update existing records directly if they already exist.
    """
    date_obj = parse_sales_upload_date(date_str)

    total_sales = 0
    any_chunk = False
    for df in iter_file_chunks(file_obj):
        any_chunk = True
        require_columns(df, "sales")

        new_sales = []
        for row in df.to_dict("records"):
            asin = str(row.get("(Child) ASIN", "")).strip()
            if not asin or asin.lower() == "nan":
                continue

            new_sales.append(
                SalesData(
                    user=user,
                    date=date_obj,
                    asin=asin,
                    pageviews=clean_number(row.get("Page Views - Total", 0)),
                    units=clean_number(row.get("Units Ordered", 0)),
                    orders=clean_number(row.get("Total Order Items", 0)),
                    revenue=float(clean_currency(row.get("Ordered Product Sales", 0))),
                )
            )

        total_sales += len(new_sales)
        if new_sales:
            for i in range(0, len(new_sales), DB_BATCH_SIZE):
                SalesData.objects.bulk_create(
                    new_sales[i : i + DB_BATCH_SIZE],
                    **get_upsert_kwargs(
                        unique_fields=["user", "date", "asin"],
                        update_fields=["pageviews", "units", "orders", "revenue"],
                    ),
                )

    if not any_chunk:
        raise ValueError("Daily Sales file is empty.")

    logger.info("[SalesData] date=%s, processed and upserted %s records.", date_obj, total_sales)
    return {date_obj}


# ---------------------------------------------------------------------------
# FBA Stock
# ---------------------------------------------------------------------------


def process_fba_stock_file(file_obj, user, id_columns=("ASIN",)):
    """
    Parse FBA stock file (Amazon/Flipkart).
    Required columns: Date, FNSKU, <product id>, MSKU, Title, Disposition,
    Starting Warehouse Balance, In Transit Between Warehouses, Receipts,
    Customer Shipments, Customer Returns, Vendor Returns,
    Warehouse Transfer In/Out, Found, Lost, Damaged, Disposed,
    Other Events, Ending Warehouse Balance, Unknown Events, Location.
    """
    required_cols = [
        "Date",
        "FNSKU",
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
    ]
    total_records = 0
    touched_dates = set()
    any_chunk = False
    row_number = 1
    for df in iter_file_chunks(file_obj):
        any_chunk = True
        col_lookup = {}
        for c in df.columns:
            key = str(c).replace("\ufeff", "").strip().lower()
            if key and key not in col_lookup:
                col_lookup[key] = c

        id_col_key = None
        for candidate in id_columns:
            key = str(candidate).strip().lower()
            if key in col_lookup:
                id_col_key = key
                break

        missing_cols = [c for c in required_cols if c.lower() not in col_lookup]
        if id_col_key is None:
            missing_cols.append("/".join(id_columns))
        if missing_cols:
            raise ValueError(
                f"FBA Stock file missing required columns: {', '.join(missing_cols)}"
            )

        records = []
        for row in df.to_dict("records"):
            row_number += 1
            asin = str(row.get(col_lookup[id_col_key], "")).strip()
            if not asin or asin.lower() == "nan":
                continue

            try:
                row_date = parse_report_date(
                    row.get(col_lookup["date"]), prefer_dayfirst=False
                )
            except Exception as exc:
                raise ValueError(
                    f"Invalid Date value in FBA Stock at row {row_number}: {exc}"
                )
            touched_dates.add(row_date)

            records.append(
                FBAStockData(
                    user=user,
                    date=row_date,
                    fnsku=str(row.get(col_lookup["fnsku"], "") or "").strip(),
                    asin=asin,
                    msku=str(row.get(col_lookup["msku"], "") or "").strip(),
                    title=str(row.get(col_lookup["title"], "") or "").strip()[:500],
                    disposition=str(row.get(col_lookup["disposition"], "") or "").strip(),
                    starting_warehouse_balance=clean_number(
                        row.get(col_lookup["starting warehouse balance"], 0)
                    ),
                    in_transit_between_warehouses=clean_number(
                        row.get(col_lookup["in transit between warehouses"], 0)
                    ),
                    receipts=clean_number(row.get(col_lookup["receipts"], 0)),
                    customer_shipments=clean_number(
                        row.get(col_lookup["customer shipments"], 0)
                    ),
                    customer_returns=clean_number(
                        row.get(col_lookup["customer returns"], 0)
                    ),
                    vendor_returns=clean_number(row.get(col_lookup["vendor returns"], 0)),
                    warehouse_transfer_in_out=clean_number(
                        row.get(col_lookup["warehouse transfer in/out"], 0)
                    ),
                    found=clean_number(row.get(col_lookup["found"], 0)),
                    lost=clean_number(row.get(col_lookup["lost"], 0)),
                    damaged=clean_number(row.get(col_lookup["damaged"], 0)),
                    disposed=clean_number(row.get(col_lookup["disposed"], 0)),
                    other_events=clean_number(row.get(col_lookup["other events"], 0)),
                    ending_warehouse_balance=clean_number(
                        row.get(col_lookup["ending warehouse balance"], 0)
                    ),
                    unknown_events=clean_number(
                        row.get(col_lookup["unknown events"], 0)
                    ),
                    location=str(row.get(col_lookup["location"], "") or "").strip(),
                )
            )

        total_records += len(records)
        if records:
            for i in range(0, len(records), DB_BATCH_SIZE):
                FBAStockData.objects.bulk_create(
                    records[i : i + DB_BATCH_SIZE],
                    **get_upsert_kwargs(
                        unique_fields=["user", "asin", "date", "disposition", "location"],
                        update_fields=[
                            "fnsku",
                            "msku",
                            "title",
                            "starting_warehouse_balance",
                            "in_transit_between_warehouses",
                            "receipts",
                            "customer_shipments",
                            "customer_returns",
                            "vendor_returns",
                            "warehouse_transfer_in_out",
                            "found",
                            "lost",
                            "damaged",
                            "disposed",
                            "other_events",
                            "ending_warehouse_balance",
                            "unknown_events",
                        ],
                    ),
                )

    if not any_chunk:
        raise ValueError("FBA Stock file is empty.")

    logger.info("[FBAStockData] Processed %s records.", total_records)
    return touched_dates


# ---------------------------------------------------------------------------
# Flex Stock
# ---------------------------------------------------------------------------


def process_flex_stock_file(file_obj, user, id_columns=("ASIN",)):
    """
    Parse Flex stock file (Amazon/Flipkart).
    Required columns: Date, <product id>, Cluster, Qty.
    """
    required_cols = ["Date", "Cluster", "Qty"]
    total_records = 0
    touched_dates = set()
    any_chunk = False
    row_number = 1
    for df in iter_file_chunks(file_obj):
        any_chunk = True
        col_lookup = {}
        for c in df.columns:
            key = str(c).replace("\ufeff", "").strip().lower()
            if key and key not in col_lookup:
                col_lookup[key] = c

        id_col_key = None
        for candidate in id_columns:
            key = str(candidate).strip().lower()
            if key in col_lookup:
                id_col_key = key
                break

        missing_cols = [c for c in required_cols if c.lower() not in col_lookup]
        if id_col_key is None:
            missing_cols.append("/".join(id_columns))
        if missing_cols:
            raise ValueError(
                f"Flex Stock file missing required columns: {', '.join(missing_cols)}"
            )

        records = []
        for row in df.to_dict("records"):
            row_number += 1
            asin = str(row.get(col_lookup[id_col_key], "")).strip()
            if not asin or asin.lower() == "nan":
                continue

            try:
                row_date = parse_report_date(
                    row.get(col_lookup["date"]), prefer_dayfirst=False
                )
            except Exception as exc:
                raise ValueError(
                    f"Invalid Date value in Flex Stock at row {row_number}: {exc}"
                )
            touched_dates.add(row_date)

            records.append(
                FlexStockData(
                    user=user,
                    date=row_date,
                    asin=asin,
                    cluster=str(row.get(col_lookup["cluster"], "") or "").strip(),
                    qty=clean_number(row.get(col_lookup["qty"], 0)),
                )
            )

        total_records += len(records)
        if records:
            for i in range(0, len(records), DB_BATCH_SIZE):
                FlexStockData.objects.bulk_create(
                    records[i : i + DB_BATCH_SIZE],
                    **get_upsert_kwargs(
                        unique_fields=["user", "asin", "date", "cluster"],
                        update_fields=["qty"],
                    ),
                )

    if not any_chunk:
        raise ValueError("Flex Stock file is empty.")

    logger.info("[FlexStockData] Processed %s records.", total_records)
    return touched_dates
