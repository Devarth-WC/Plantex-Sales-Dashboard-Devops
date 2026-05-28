import pandas as pd
from datetime import datetime, timedelta

from .utils import read_tabular_file


def validate_sales(filepath, master_data):
    try:
        df = read_tabular_file(filepath, low_memory=False)
    except Exception as e:
        return [
            {
                "Row": "-",
                "Column": "-",
                "Value": "-",
                "Message": f"Failed to read file: {str(e)}",
            }
        ]

    errors = []

    # Extract master lists
    fc_master = set(master_data["fc_list"]).union(set(master_data["flex_list"]))
    asin_master = set(master_data["asin_list"])
    pincode_master = set(master_data["pincode_list"])

    # Process Dates
    today = datetime.now().date()
    cutoff_date = today - timedelta(days=120)

    # 1. FC validation
    if "FC CODE" in df.columns:
        invalid_fcs = df[df["FC CODE"].notna() & ~df["FC CODE"].isin(fc_master)]
        for idx, row in invalid_fcs.iterrows():
            errors.append(
                {
                    "Row": idx + 2,
                    "Column": "FC CODE",
                    "Value": str(row["FC CODE"]),
                    "Message": "FC CODE not found in master",
                    "ASIN": str(row.get("ASIN", "")),
                }
            )

    # 2. Pincode validation
    if "Shipment To Postal Code" in df.columns:
        pincodes = df["Shipment To Postal Code"]
        mask_notna = pincodes.notna()
        
        def check_pincode(p):
            try:
                return float(p) not in pincode_master
            except ValueError:
                return True
                
        invalid_mask = mask_notna & pincodes.apply(check_pincode)
        for idx, row in df[invalid_mask].iterrows():
            p = row["Shipment To Postal Code"]
            try:
                float(p)
                msg = "Pincode not found in master"
            except ValueError:
                msg = "Invalid Pincode format"
            errors.append(
                {
                    "Row": idx + 2,
                    "Column": "Shipment To Postal Code",
                    "Value": str(p),
                    "Message": msg,
                    "ASIN": str(row.get("ASIN", "")),
                }
            )

    # 3. ASIN validation
    if "ASIN" in df.columns:
        invalid_asins = df[df["ASIN"].notna() & ~df["ASIN"].isin(asin_master)]
        for idx, row in invalid_asins.iterrows():
            errors.append(
                {
                    "Row": idx + 2,
                    "Column": "ASIN",
                    "Value": str(row["ASIN"]),
                    "Message": "ASIN not found in master",
                    "ASIN": str(row["ASIN"]),
                }
            )

    # 4. Sales Report days: dates > Today+120 days
    if "Customer Shipment Date" in df.columns:
        # Vectorized datetime parsing
        dates = pd.to_datetime(df["Customer Shipment Date"], dayfirst=True, format="mixed", errors="coerce")
        # Find valid parsed dates that are before the cutoff
        invalid_mask = dates.notna() & (dates.dt.date < cutoff_date)
        for idx, row in df[invalid_mask].iterrows():
            errors.append(
                {
                    "Row": idx + 2,
                    "Column": "Customer Shipment Date",
                    "Value": str(row["Customer Shipment Date"]),
                    "Message": f"Date is before Today - 120 days ({cutoff_date})",
                    "ASIN": str(row.get("ASIN", "")),
                }
            )

    return errors


def validate_shipment(filepath, master_data):
    try:
        df = read_tabular_file(filepath)
    except Exception as e:
        return [
            {
                "Row": "-",
                "Column": "-",
                "Value": "-",
                "Message": f"Failed to read file: {str(e)}",
            }
        ]

    errors = []

    # Data cleaning: Remove rows where STATUS is "Closed", "STATUS" (header), or "(Blanks)"
    if "STATUS" in df.columns:
        invalid_statuses = ["Closed", "STATUS", "(Blanks)"]
        df = df[~df["STATUS"].astype(str).str.strip().isin(invalid_statuses)]
        df = df.dropna(subset=["STATUS"])
        df = df.reset_index(drop=True)

    fc_master = set(master_data["fc_list"]).union(set(master_data["flex_list"]))
    asin_master = set(master_data["asin_list"])
    cluster_master = set(master_data["cluster_list"])

    today = datetime.now().date()
    cutoff_date = today - timedelta(days=21)

    # 1. ASIN validation
    if "ASIN" in df.columns:
        invalid_asins = df[df["ASIN"].notna() & ~df["ASIN"].isin(asin_master)]
        for idx, row in invalid_asins.iterrows():
            errors.append(
                {
                    "Row": idx + 2,
                    "Column": "ASIN",
                    "Value": str(row["ASIN"]),
                    "Message": "ASIN not found in master",
                    "Shipment_id": str(row.get("ID", "")),
                    "ASIN": str(row["ASIN"]),
                }
            )

    # 2. FC validation
    fc_col = "FC CODE" if "FC CODE" in df.columns else ("FC" if "FC" in df.columns else None)
    if fc_col:
        invalid_fcs = df[df[fc_col].notna() & ~df[fc_col].isin(fc_master)]
        for idx, row in invalid_fcs.iterrows():
            errors.append(
                {
                    "Row": idx + 2,
                    "Column": "FC CODE/FC",
                    "Value": str(row[fc_col]),
                    "Message": "FC CODE/FC not found in master",
                    "Shipment_id": str(row.get("ID", "")),
                    "ASIN": str(row.get("ASIN", "")),
                }
            )

    # 3. Cluster validation
    if "CLUSTER" in df.columns:
        invalid_clusters = df[df["CLUSTER"].notna() & ~df["CLUSTER"].isin(cluster_master)]
        for idx, row in invalid_clusters.iterrows():
            errors.append(
                {
                    "Row": idx + 2,
                    "Column": "CLUSTER",
                    "Value": str(row["CLUSTER"]),
                    "Message": "Cluster not found in master",
                    "Shipment_id": str(row.get("ID", "")),
                    "ASIN": str(row.get("ASIN", "")),
                }
            )

    # 4. Loading Date Flag
    if "LOADING DATE" in df.columns:
        dates = pd.to_datetime(df["LOADING DATE"], dayfirst=True, format="mixed", errors="coerce")
        invalid_mask = dates.notna() & (dates.dt.date < cutoff_date)
        for idx, row in df[invalid_mask].iterrows():
            errors.append(
                {
                    "Row": idx + 2,
                    "Column": "LOADING DATE",
                    "Value": str(dates[idx].date()),
                    "Message": f"Loading Date is before Today-21 days ({cutoff_date}), flagged as too old",
                    "Shipment_id": str(row.get("ID", "")),
                    "ASIN": str(row.get("ASIN", "")),
                }
            )

    return errors


def validate_stock(filepath, master_data):
    try:
        df = read_tabular_file(filepath)
    except Exception as e:
        return [
            {
                "Row": "-",
                "Column": "-",
                "Value": "-",
                "Message": f"Failed to read file: {str(e)}",
            }
        ]

    errors = []

    fc_master = set(master_data["fc_list"]).union(set(master_data["flex_list"]))
    asin_master = set(master_data["asin_list"])

    today = datetime.now().date()
    min_date = today - timedelta(days=5)
    max_date = today

    # 1. ASIN validation
    if "ASIN" in df.columns:
        invalid_asins = df[df["ASIN"].notna() & ~df["ASIN"].isin(asin_master)]
        for idx, row in invalid_asins.iterrows():
            errors.append(
                {
                    "Row": idx + 2,
                    "Column": "ASIN",
                    "Value": str(row["ASIN"]),
                    "Message": "ASIN not found in master",
                }
            )

    # 2. FC (Location) validation - Support both 'FC CODE' and 'Location'
    fc_col = "FC CODE" if "FC CODE" in df.columns else ("Location" if "Location" in df.columns else None)
    if fc_col:
        invalid_fcs = df[df[fc_col].notna() & ~df[fc_col].isin(fc_master)]
        for idx, row in invalid_fcs.iterrows():
            errors.append(
                {
                    "Row": idx + 2,
                    "Column": "FC CODE/Location",
                    "Value": str(row[fc_col]),
                    "Message": "FC CODE/Location not found in master",
                }
            )

    # 3. Date Range validation - User specified MM-DD-YYYY format
    if "Date" in df.columns:
        dates = pd.to_datetime(df["Date"], dayfirst=False, format="mixed", errors="coerce")
        # Condition: Date is parsed correctly but NOT in [min_date, max_date]
        invalid_mask = dates.notna() & ~((dates.dt.date >= min_date) & (dates.dt.date <= max_date))
        for idx, row in df[invalid_mask].iterrows():
            errors.append(
                {
                    "Row": idx + 2,
                    "Column": "Date",
                    "Value": str(dates[idx].date()),
                    "Message": f"Date not in range Today-5 to Today ({min_date} to {max_date})",
                }
            )

    return errors


def validate_lis(filepath, master_data):
    try:
        df = read_tabular_file(filepath)
    except Exception as e:
        return [
            {
                "Row": "-",
                "Column": "-",
                "Value": "-",
                "Message": f"Failed to read file: {str(e)}",
            }
        ]

    errors = []

    asin_master = set(master_data["asin_list"])

    if "ASIN" in df.columns:
        invalid_asins = df[df["ASIN"].notna() & ~df["ASIN"].astype(str).str.strip().isin(asin_master)]
        for idx, row in invalid_asins.iterrows():
            errors.append(
                {
                    "Row": idx + 2,
                    "Column": "ASIN",
                    "Value": str(row["ASIN"]),
                    "Message": "ASIN from LIS Report not found in Assortment Master file",
                }
            )

    return errors
