import datetime


UPLOAD_SCHEMAS = {
    "category": {
        "label": "Category Mapping",
        "required": ["ASIN", "Portfolio", "Category", "Subcategory", "Skus"],
    },
    "price": {"label": "Pricing Data", "required": ["ASIN", "Price"]},
    "spend": {
        "label": "Ads Spends",
        "required": ["Date", "Ad Account", "Ad Type", "ASIN", "Spend"],
    },
    "sales": {
        "label": "Daily Sales",
        "required": [
            "(Child) ASIN",
            "Page Views - Total",
            "Units Ordered",
            "Ordered Product Sales",
            "Total Order Items",
        ],
        "date_format": "%d-%m-%Y",
    },
    "fk_search_traffic": {
        "label": "FK Search Traffic",
        "required": [
            "Listing Id",
            "SKU Id",
            "Vertical",
            "Impression Date",
            "Product Clicks",
            "Sales",
            "Revenue",
        ],
    },
    "fk_price": {
        "label": "FK Price",
        "required": ["Flipkart Serial Number", "Deal"],
    },
    "fk_pla": {
        "label": "FK PLA",
        "required": ["Campaign ID", "Advertised FSN ID", "Ad Spend"],
    },
    "fk_fba_stock": {
        "label": "FK FBA Stock",
        "required": ["Date", "FSN", "Live on Website"],
    },
    "fk_inventory": {"label": "FK Inventory", "required": ["FSN", "Qty"]},
}


def validate_file_type(file_type):
    allowed = {
        "sales",
        "category",
        "spend",
        "price",
        "fba_stock",
        "flex_stock",
        "fk_search_traffic",
        "fk_category",
        "fk_price",
        "fk_pla",
        "fk_fba_stock",
        "fk_inventory",
    }
    if file_type not in allowed:
        raise ValueError(f"Unsupported file_type: {file_type}")


def required_columns(file_type):
    return list(UPLOAD_SCHEMAS.get(file_type, {}).get("required", []))


def label_for(file_type, default=None):
    return UPLOAD_SCHEMAS.get(file_type, {}).get("label", default or file_type)


def require_columns(df, file_type, required=None):
    expected = required if required is not None else required_columns(file_type)
    missing = [col for col in expected if col not in df.columns]
    if missing:
        raise ValueError(
            f"{label_for(file_type)} missing required columns: {', '.join(missing)}"
        )


def parse_sales_upload_date(date_str):
    try:
        return datetime.datetime.strptime(date_str, "%d-%m-%Y").date()
    except ValueError as exc:
        raise ValueError(
            f"Invalid Date format '{date_str}' in Daily Sales filename. Please strictly use DD-MM-YYYY.csv format."
        ) from exc
