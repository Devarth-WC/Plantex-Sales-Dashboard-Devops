"""
Utility functions for the replenishment app.
Kept separate from views to avoid circular imports with tasks.py.
"""

import pandas as pd
from apps.dashboard.utils import extract_days


def read_tabular_file(path, **kwargs):
    """Read CSV/XLSX/XLSM/XLS input with a consistent fallback strategy."""
    ext = str(path or "").lower().rsplit(".", 1)
    ext = f".{ext[-1]}" if len(ext) == 2 else ""

    if ext == ".csv":
        for enc in ("utf-8", "latin-1", "cp1252"):
            try:
                return pd.read_csv(path, encoding=enc, **kwargs)
            except UnicodeDecodeError:
                continue
        return pd.read_csv(path, **kwargs)

    if ext in {".xlsx", ".xlsm", ".xls"}:
        try:
            return pd.read_excel(path, **kwargs)
        except Exception:
            return pd.read_excel(path, engine="openpyxl", **kwargs)

    raise ValueError(
        f"Unsupported file format '{ext}'. Only .csv, .xlsx, .xlsm, and .xls are supported."
    )


def generate_master_data(
    fc_mapping_path, pincode_path, product_details_path, input_sheet_path
):
    """
    Read the 4 mapping/config files and produce the master_data dict
    used by the validation functions.
    """
    try:
        fc_cluster_mapping_3 = read_tabular_file(fc_mapping_path)
        pincode_cluster = read_tabular_file(pincode_path)
        product_details = read_tabular_file(product_details_path)
        input_sheet_1 = read_tabular_file(input_sheet_path)

        # Clean columns
        fc_cluster_mapping_3.columns = fc_cluster_mapping_3.columns.str.strip()
        pincode_cluster.columns = pincode_cluster.columns.str.strip()
        product_details.columns = product_details.columns.str.strip()
        input_sheet_1.columns = input_sheet_1.columns.str.strip()

        # FC lists
        fc_list = (
            fc_cluster_mapping_3[fc_cluster_mapping_3["FC TYPE"] == "AMAZON"]["FC CODE"]
            .dropna()
            .unique()
            .tolist()
        )
        flex_list = (
            fc_cluster_mapping_3[fc_cluster_mapping_3["FC TYPE"] == "FLEX"]["FC CODE"]
            .dropna()
            .unique()
            .tolist()
        )

        fc_ixd_list = ["ISK3", "BLR4", "DED5", "DED3", "HBX1", "HBX2"]
        fc_local_list = list(set(fc_list) - set(fc_ixd_list))

        # Other lists
        asin_list = product_details["ASIN"].dropna().unique().tolist()
        cluster_list = pincode_cluster["Fulfilment Cluster"].dropna().unique().tolist()
        pincode_list = pincode_cluster["PIN CODE"].dropna().unique().tolist()

        # Date parameters
        p0_day = extract_days(
            input_sheet_1[input_sheet_1["Particular"] == "P0 Demand DOC"][
                "Value"
            ].values[0]
        )
        p1_day = extract_days(
            input_sheet_1[input_sheet_1["Particular"] == "P1 Demand DOC"][
                "Value"
            ].values[0]
        )
        p2_day = extract_days(
            input_sheet_1[input_sheet_1["Particular"] == "P2 Demand DOC"][
                "Value"
            ].values[0]
        )
        sales_day_count = extract_days(
            input_sheet_1[input_sheet_1["Particular"] == "Sale Report Days"][
                "Value"
            ].values[0]
        )

        return {
            "fc_list": fc_list,
            "flex_list": flex_list,
            "fc_ixd_list": fc_ixd_list,
            "fc_local_list": fc_local_list,
            "asin_list": asin_list,
            "cluster_list": cluster_list,
            "pincode_list": pincode_list,
            "p0_day": p0_day,
            "p1_day": p1_day,
            "p2_day": p2_day,
            "sales_day_count": sales_day_count,
        }
    except Exception as e:
        raise Exception(f"Failed to generate master data from mapping files: {str(e)}")
