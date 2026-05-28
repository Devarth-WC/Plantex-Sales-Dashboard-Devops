import logging
import re

from apps.dashboard.models import (
    Flipkartfba,
    FlipkartCategoryMap,
    FlipkartInventoryStock,
    FlipkartPLA,
    FlipkartPrice,
    FlipkartSearchTraffic,
)
from apps.dashboard.utils import clean_currency, clean_number
from apps.upload.parsers import (
    extract_fk_report_date_from_metadata as _extract_fk_report_date_from_metadata,
    iter_file_chunks,
    parse_report_date,
)
from apps.upload.schema import require_columns

from .service_common import DB_BATCH_SIZE, get_upsert_kwargs

logger = logging.getLogger(__name__)


def process_fk_inventory_file(file_obj, user):
    """
    Parse FK Inventory file (FK.xlsx).
    Required columns: PRODUCTS STATUS, PRODUCTS TYPE, SKU, FSN, Qty.

    The FK file does not contain a Date column; the date is derived from:
      1. An explicit "Date" column if present, or
      2. The file's last-modified timestamp on disk, or
      3. The current date as a fallback.
    All records are tagged with this date in DD-MM-YYYY format.
    """
    import datetime
    import os

    required_cols = ["FSN", "Qty"]
    any_chunk = False
    total_records = 0
    # Derive date from the file
    file_date = None
    if hasattr(file_obj, "name") and os.path.exists(file_obj.name):
        try:
            mtime = os.path.getmtime(file_obj.name)
            file_date = datetime.date.fromtimestamp(mtime)
        except Exception:
            pass
    if file_date is None:
        file_date = datetime.date.today()

    touched_dates = set()

    for df in iter_file_chunks(file_obj):
        any_chunk = True
        col_lookup = {}
        for c in df.columns:
            key = str(c).replace("\ufeff", "").strip().lower()
            if key and key not in col_lookup:
                col_lookup[key] = c

        missing_cols = [c for c in required_cols if c.lower() not in col_lookup]
        if missing_cols:
            raise ValueError(
                f"FK Inventory file missing required columns: {', '.join(missing_cols)}"
            )

        # If the file has an explicit Date column, use it per-row
        has_date_col = "date" in col_lookup

        records = []
        for row in df.to_dict("records"):
            fsn = str(row.get(col_lookup.get("fsn", "FSN"), "")).strip()
            if not fsn or fsn.lower() == "nan":
                continue

            # Determine row-level date
            if has_date_col:
                raw_date = row.get(col_lookup["date"])
                if raw_date is not None and str(raw_date).strip():
                    try:
                        row_date = parse_report_date(raw_date, prefer_dayfirst=True)
                    except Exception:
                        row_date = file_date
                else:
                    row_date = file_date
            else:
                row_date = file_date

            touched_dates.add(row_date)

            sku = str(row.get(col_lookup.get("sku", "SKU"), "") or "").strip()
            product_status = str(
                row.get(col_lookup.get("products status", "PRODUCTS STATUS"), "") or ""
            ).strip()
            product_type = str(
                row.get(col_lookup.get("products type", "PRODUCTS TYPE"), "") or ""
            ).strip()
            qty = clean_number(row.get(col_lookup.get("qty", "Qty"), 0))

            records.append(
                FlipkartInventoryStock(
                    user=user,
                    date=row_date,
                    fsn=fsn,
                    sku=sku if sku.lower() != "nan" else "",
                    product_status=product_status if product_status.lower() != "nan" else "",
                    product_type=product_type if product_type.lower() != "nan" else "",
                    qty=qty,
                )
            )

        total_records += len(records)
        if records:
            for i in range(0, len(records), DB_BATCH_SIZE):
                FlipkartInventoryStock.objects.bulk_create(
                    records[i : i + DB_BATCH_SIZE],
                    **get_upsert_kwargs(
                        unique_fields=["user", "fsn", "date"],
                        update_fields=["sku", "product_status", "product_type", "qty"],
                    ),
                )

    if not any_chunk:
        raise ValueError("FK Inventory file is empty.")

    logger.info("[FlipkartInventoryStock] Processed %s uploaded rows.", total_records)
    return touched_dates


def process_fk_fba_stock_file(file_obj, user):
    """
    Parse Flipkart FBA current inventory report.
    Required columns:
    - Date
    - FSN
    - Live on Website
    """
    required_cols = ["Date", "FSN", "Live on Website"]
    any_chunk = False
    total_records = 0
    touched_dates = set()
    row_number = 1

    for df in iter_file_chunks(file_obj):
        any_chunk = True
        col_lookup = {}
        for c in df.columns:
            key = str(c).replace("\ufeff", "").strip().lower()
            if key and key not in col_lookup:
                col_lookup[key] = c

        missing_cols = [c for c in required_cols if c.lower() not in col_lookup]
        if missing_cols:
            raise ValueError(
                "FK FBA Stock file missing required columns: "
                + ", ".join(missing_cols)
            )

        date_col = col_lookup["date"]
        fsn_col = col_lookup["fsn"]
        live_col = col_lookup["live on website"]
        warehouse_col = col_lookup.get("warehouse id")
        sku_col = col_lookup.get("sku")
        title_col = col_lookup.get("title")

        records = []
        for row in df.to_dict("records"):
            row_number += 1
            fsn = str(row.get(fsn_col, "") or "").strip()
            if not fsn or fsn.lower() == "nan":
                continue

            raw_date = row.get(date_col)
            if raw_date is None or str(raw_date).strip() == "":
                raise ValueError(
                    f"Missing Date value in FK FBA Stock at row {row_number}."
                )
            try:
                row_date = parse_report_date(raw_date, prefer_dayfirst=True)
            except Exception as exc:
                raise ValueError(
                    f"Invalid Date value in FK FBA Stock at row {row_number}: {exc}"
                )

            touched_dates.add(row_date)
            live_on_website_qty = clean_number(row.get(live_col, 0))
            location = (
                str(row.get(warehouse_col, "") or "").strip() if warehouse_col else ""
            )
            msku = str(row.get(sku_col, "") or "").strip() if sku_col else ""
            title = str(row.get(title_col, "") or "").strip() if title_col else ""
            records.append(
                Flipkartfba(
                    user=user,
                    date=row_date,
                    fsn=fsn,
                    warehouse_id=location,
                    sku=msku,
                    title=title[:500],
                    listing_id=str(row.get(col_lookup.get("listing id"), "") or "").strip()
                    if col_lookup.get("listing id")
                    else "",
                    brand=str(row.get(col_lookup.get("brand"), "") or "").strip()
                    if col_lookup.get("brand")
                    else "",
                    flipkart_selling_price=clean_currency(
                        row.get(col_lookup.get("flipkart selling price"), 0)
                        if col_lookup.get("flipkart selling price")
                        else 0
                    ),
                    live_on_website=live_on_website_qty,
                )
            )

        total_records += len(records)
        if records:
            for i in range(0, len(records), DB_BATCH_SIZE):
                Flipkartfba.objects.bulk_create(
                    records[i : i + DB_BATCH_SIZE],
                    **get_upsert_kwargs(
                        unique_fields=["user", "date", "fsn", "warehouse_id"],
                        update_fields=[
                            "sku",
                            "title",
                            "listing_id",
                            "brand",
                            "flipkart_selling_price",
                            "live_on_website",
                        ],
                    ),
                )

    if not any_chunk:
        raise ValueError("FK FBA Stock file is empty.")

    logger.info("[FK FBA Stock] Processed %s records.", total_records)
    return touched_dates


# ===========================================================================
# SLIM FLIPKART PROCESSING FUNCTIONS
# ===========================================================================

# ---------------------------------------------------------------------------
# FK Search Traffic Report
# ---------------------------------------------------------------------------


def process_fk_search_traffic(file_obj, user):
    """
    Parse Flipkart Search Traffic Report (.xlsx).
    Extracts FSN from Listing Id using Mid(Listing Id, 4, 16) → listing_id[3:19].
    Saves per-FSN per-date traffic & sales data.
    """
    total_records = 0
    any_chunk = False
    touched_dates = set()
    all_key_totals = {}
    row_number = 1
    for df in iter_file_chunks(file_obj):
        any_chunk = True
        require_columns(df, "fk_search_traffic")

        for row in df.to_dict("records"):
            row_number += 1
            listing_id = str(row.get("Listing Id", "")).strip()
            if not listing_id or listing_id.lower() == "nan" or len(listing_id) < 19:
                continue

            fsn = listing_id[3:19]  # Mid(Listing Id, 4, 16)
            try:
                row_date = parse_report_date(row.get("Impression Date"), prefer_dayfirst=False)
            except Exception as exc:
                raise ValueError(
                    f"Invalid Impression Date in FK Search Traffic at row {row_number}: {exc}"
                )
            touched_dates.add(row_date)

            sku = str(row.get("SKU Id", "") or "").strip().replace('"', "")
            sku = re.sub(r"(?i)^SKU:\s*", "", sku)
            key = (fsn, row_date)
            vertical = str(row.get("Vertical", "") or "").strip()
            if key not in all_key_totals:
                all_key_totals[key] = {
                    "fsn": fsn,
                    "date": row_date,
                    "sku": sku,
                    "vertical": vertical,
                    "page_views": 0,
                    "product_clicks": 0,
                    "sales": 0,
                    "revenue": 0.0,
                }
            else:
                # Preserve first non-empty descriptive fields for consistency.
                if not all_key_totals[key]["sku"] and sku:
                    all_key_totals[key]["sku"] = sku
                if not all_key_totals[key]["vertical"] and vertical:
                    all_key_totals[key]["vertical"] = vertical

            clicks = clean_number(row.get("Product Clicks", 0))
            all_key_totals[key]["page_views"] += clicks
            all_key_totals[key]["product_clicks"] += clicks
            all_key_totals[key]["sales"] += clean_number(row.get("Sales", 0))
            all_key_totals[key]["revenue"] += float(clean_currency(row.get("Revenue", 0)))

    if not any_chunk:
        raise ValueError("FK Search Traffic file is empty.")

    records = []
    for payload in all_key_totals.values():
        records.append(
            FlipkartSearchTraffic(
                user=user,
                fsn=payload["fsn"],
                sku=payload["sku"],
                vertical=payload["vertical"],
                date=payload["date"],
                page_views=payload["page_views"],
                product_clicks=payload["product_clicks"],
                sales=payload["sales"],
                revenue=payload["revenue"],
            )
        )
    total_records = len(records)
    if records:
        for i in range(0, len(records), DB_BATCH_SIZE):
            FlipkartSearchTraffic.objects.bulk_create(
                records[i : i + DB_BATCH_SIZE],
                **get_upsert_kwargs(
                    unique_fields=["user", "fsn", "date"],
                    update_fields=[
                        "sku",
                        "vertical",
                        "page_views",
                        "product_clicks",
                        "sales",
                        "revenue",
                    ],
                ),
            )

    logger.info("[FK SearchTraffic] Processed %s records.", total_records)
    return touched_dates


# ---------------------------------------------------------------------------
# FK Category Report
# ---------------------------------------------------------------------------


def process_fk_category(file_obj, user):
    """
    Parse Flipkart Category Dashboard (.xlsx).
    Expected columns:
    - FSN ID
    - asin (optional; ignored)
    - SKU
    - Portfolio
    - Category
    - Sub Category
    - Vertical (optional; ignored)
    - Product Status (optional)
    """
    total_records = 0
    any_chunk = False
    touched_fsns = set()
    for df in iter_file_chunks(file_obj):
        any_chunk = True
        col_lookup = {}
        for original_col in df.columns:
            normalized = re.sub(r"[^a-z0-9]+", "", str(original_col).strip().lower())
            if normalized and normalized not in col_lookup:
                col_lookup[normalized] = original_col

        def resolve_col(*aliases):
            for alias in aliases:
                normalized = re.sub(r"[^a-z0-9]+", "", alias.strip().lower())
                if normalized in col_lookup:
                    return col_lookup[normalized]
            return None

        fsn_col = resolve_col("FSN ID")
        _asin_col = resolve_col("asin", "ASIN")
        sku_col = resolve_col("SKU")
        portfolio_col = resolve_col("Portfolio")
        category_col = resolve_col("Category")
        subcategory_col = resolve_col("Sub Category", "Subcategory")
        _vertical_col = resolve_col("Vertical")
        product_status_col = resolve_col("Product Status")

        missing = []
        if not fsn_col:
            missing.append("FSN ID")
        if not sku_col:
            missing.append("SKU")
        if not portfolio_col:
            missing.append("Portfolio")
        if not category_col:
            missing.append("Category")
        if not subcategory_col:
            missing.append("Sub Category")
        if missing:
            raise ValueError(f"FK Category missing columns: {', '.join(missing)}")

        records = []
        for row in df.to_dict("records"):
            fsn = str(row.get(fsn_col, "")).strip()
            if not fsn or fsn.lower() == "nan":
                continue
            touched_fsns.add(fsn)

            raw_status = (
                str(row.get(product_status_col, "") or "").strip()
                if product_status_col
                else ""
            )
            normalized_status = ""
            status_lower = raw_status.lower()
            if status_lower in ("continued", "continue", "continued/pack of not sales"):
                normalized_status = "Continued"
            elif status_lower in ("discontinued", "discontinue"):
                normalized_status = "Discontinued"

            records.append(
                FlipkartCategoryMap(
                    user=user,
                    fsn=fsn,
                    sku=str(row.get(sku_col, "") or "").strip() if sku_col else "",
                    portfolio=str(row.get(portfolio_col, "") or "").strip(),
                    category=str(row.get(category_col, "") or "").strip(),
                    subcategory=str(row.get(subcategory_col, "") or "").strip(),
                    product_status=normalized_status,
                )
            )

        total_records += len(records)
        if records:
            for i in range(0, len(records), DB_BATCH_SIZE):
                FlipkartCategoryMap.objects.bulk_create(
                    records[i : i + DB_BATCH_SIZE],
                    **get_upsert_kwargs(
                        unique_fields=["user", "fsn"],
                        update_fields=[
                            "sku",
                            "portfolio",
                            "category",
                            "subcategory",
                            "product_status",
                        ],
                    ),
                )

    if not any_chunk:
        raise ValueError("FK Category file is empty.")

    logger.info("[FK Category] Processed %s records.", total_records)
    return touched_fsns


# ---------------------------------------------------------------------------
# FK Price Report
# ---------------------------------------------------------------------------


def process_fk_price(file_obj, user):
    """
    Parse Flipkart Price file (.xlsx).
    Columns: Flipkart Serial Number → fsn, Deal → price.
    """
    total_records = 0
    any_chunk = False
    touched_fsns = set()
    for df in iter_file_chunks(file_obj):
        any_chunk = True
        require_columns(df, "fk_price")

        records = []
        for row in df.to_dict("records"):
            fsn = str(row.get("Flipkart Serial Number", "")).strip().replace('"', "")
            if not fsn or fsn.lower() == "nan":
                continue
            touched_fsns.add(fsn)
            records.append(
                FlipkartPrice(
                    user=user,
                    fsn=fsn,
                    price=float(clean_currency(row.get("Deal", 0))),
                )
            )

        total_records += len(records)
        if records:
            for i in range(0, len(records), DB_BATCH_SIZE):
                FlipkartPrice.objects.bulk_create(
                    records[i : i + DB_BATCH_SIZE],
                    **get_upsert_kwargs(
                        unique_fields=["user", "fsn"], update_fields=["price"]
                    ),
                )

    if not any_chunk:
        raise ValueError("FK Price file is empty.")

    logger.info("[FK Price] Processed %s records.", total_records)
    return touched_fsns


# ---------------------------------------------------------------------------
# FK PLA FSN Report
# ---------------------------------------------------------------------------


def process_fk_pla(file_obj, user):
    """
    Parse Flipkart PLA FSN Report (.csv).
    File has 2 metadata rows then the header row.
    Columns: Campaign ID, Advertised FSN ID, Ad Spend.
    """
    total_records = 0
    any_chunk = False
    all_key_spend = {}
    report_date = _extract_fk_report_date_from_metadata(file_obj)
    if report_date is None:
        raise ValueError("FK PLA metadata missing Start Time/End Time.")

    for df in iter_file_chunks(file_obj, skiprows=2):
        any_chunk = True
        require_columns(df, "fk_pla")

        for row in df.to_dict("records"):
            campaign_id = str(row.get("Campaign ID", "")).strip()
            fsn_id = str(row.get("Advertised FSN ID", "")).strip().replace('"', "")
            if not fsn_id or fsn_id.lower() == "nan":
                continue

            key = (campaign_id, fsn_id, report_date)
            all_key_spend[key] = all_key_spend.get(key, 0.0) + float(
                clean_currency(row.get("Ad Spend", 0))
            )

    if not any_chunk:
        raise ValueError("FK PLA file is empty.")

    records = []
    for key, spend in all_key_spend.items():
        records.append(
            FlipkartPLA(
                user=user,
                campaign_id=key[0],
                fsn_id=key[1],
                date=key[2],
                ad_spend=spend,
            )
        )
    total_records = len(records)
    if records:
        for i in range(0, len(records), DB_BATCH_SIZE):
            FlipkartPLA.objects.bulk_create(
                records[i : i + DB_BATCH_SIZE],
                **get_upsert_kwargs(
                    unique_fields=["user", "campaign_id", "fsn_id", "date"],
                    update_fields=["ad_spend"],
                ),
            )

    logger.info("[FK PLA] Processed %s records.", total_records)
    return {report_date}
