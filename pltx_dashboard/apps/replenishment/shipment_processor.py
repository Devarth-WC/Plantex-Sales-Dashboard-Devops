import pandas as pd
import numpy as np
import os

from .utils import read_tabular_file as load_data


def generate_shipment_report(shipment_file, mapping_file, output_file):
    print(f"Loading Shipment data from {shipment_file}...")
    try:
        df = load_data(shipment_file)
    except Exception as e:
        print(f"Error reading shipment file: {e}")
        return

    print(f"Loading FC Mapping from {mapping_file}...")
    try:
        mapping_df = load_data(mapping_file)
    except Exception as e:
        print(f"Error reading mapping file: {e}")
        return

    # Strip and uppercase column names to handle case sensitivity
    df.columns = df.columns.astype(str).str.strip().str.upper()
    mapping_df.columns = mapping_df.columns.astype(str).str.strip().str.upper()

    # Data cleaning: Remove all rows where Status is “Closed”, "(Blanks)", or "STATUS"
    if "STATUS" in df.columns:
        invalid_statuses = ["Closed", r"\(Blanks\)", "STATUS"]
        df = df[
            ~df["STATUS"]
            .astype(str)
            .str.strip()
            .str.contains("|".join(invalid_statuses), case=False, na=False)
        ]
        df = df.dropna(subset=["STATUS"])

    # Check required columns
    required_cols = [
        "ASIN",
        "CLUSTER",
        "FC",
        "STATUS",
        "FINAL QTY",
        "ID",
        "APPOINTMENT DATE",
    ]
    for col in required_cols:
        if col not in df.columns:
            print(f"Error: Missing required column '{col}' in Shipment file.")
            print(f"Available columns: {list(df.columns)}")
            return

    # Build FC Replenishment Type lists from mapping
    fc_list = (
        mapping_df[mapping_df["FC TYPE"] == "AMAZON"]["FC CODE"]
        .dropna()
        .unique()
        .tolist()
    )
    fc_ixd_list = ["ISK3", "BLR4", "DED5", "DED3", "HBX1", "HBX2"]
    fc_local_list = list(set(fc_list) - set(fc_ixd_list))

    # --- Vectorized FC Replenishment Type mapping ---
    fc_series = df["FC"].astype(str).str.strip()
    df["FC Replenishment Type"] = np.where(
        fc_series.isin(fc_local_list), "DFC",
        np.where(fc_series.isin(fc_ixd_list), "IXD", "")
    )

    # Convert FINAL QTY to numeric before calculation
    df["FINAL QTY"] = pd.to_numeric(df["FINAL QTY"], errors="coerce").fillna(0)

    # Extract clean status
    df["STATUS_CLEAN"] = df["STATUS"].astype(str).str.strip()

    # --- Vectorized quantity calculations based on status ---
    qty = df["FINAL QTY"]
    status = df["STATUS_CLEAN"]

    df["Appointment Pending Qty"] = np.where(status == "Appointment Pending", qty, 0)
    df["Upcoming Shipment Qty"]   = np.where(status == "Upcoming", qty, 0)

    inbound_statuses = ["Receiving", "Intransit", "In Transit", "Inbound"]
    df["Receiving + Intransit Qty"] = np.where(status.isin(inbound_statuses), qty, 0)
    df["Receiving Qty"]             = np.where(status == "Receiving", qty, 0)

    intransit_statuses = ["Intransit", "In Transit", "Inbound"]
    df["Intransit Qty"] = np.where(status.isin(intransit_statuses), qty, 0)

    # Format Appointment Date — vectorized ID + date combination
    appt_date_str = df["APPOINTMENT DATE"].astype(str).str.strip().str.split(" ").str[0]
    id_str = df["ID"].astype(str).str.strip()
    df["Shipment_Detail"] = '"' + id_str + '":"' + appt_date_str + '"'

    # Convert APPOINTMENT DATE to actual datetime objects for calculation
    df["APPT_DATE_DT"] = pd.to_datetime(df["APPOINTMENT DATE"], errors="coerce")

    # --- Vectorized DFC Next Arrival Date ---
    today = pd.Timestamp.now().normalize()
    dfc_mask = (
        (df["FC Replenishment Type"] == "DFC")
        & df["APPT_DATE_DT"].notna()
        & (df["APPT_DATE_DT"] >= today)
    )
    df["DFC Next Arrival Date"] = np.where(dfc_mask, df["APPT_DATE_DT"], pd.NaT)

    # Grouping by ASIN
    # We will include CLUSTER and FC Replenishment Type in the group to maintain geographic accuracy
    groupby_cols = ["ASIN", "CLUSTER", "FC Replenishment Type"]

    agg_funcs = {
        "Appointment Pending Qty": "sum",
        "Upcoming Shipment Qty": "sum",
        "Receiving + Intransit Qty": "sum",
        "Receiving Qty": "sum",
        "Intransit Qty": "sum",
        "Shipment_Detail": lambda x: "[" + ", ".join(x.dropna().unique()) + "]",
        "DFC Next Arrival Date": "min",
    }

    print("Aggregating grouped data...")
    report_df = df.groupby(groupby_cols, dropna=False).agg(agg_funcs).reset_index()

    # Format the DFC Next Arrival Date back to string
    report_df["DFC Next Arrival Date"] = pd.to_datetime(report_df["DFC Next Arrival Date"], errors="coerce")
    report_df["DFC Next Arrival Date"] = (
        report_df["DFC Next Arrival Date"].dt.strftime("%Y-%m-%d").fillna("")
    )

    # Rename columns to match requested output
    report_df.rename(
        columns={"Shipment_Detail": "Shipment ID", "CLUSTER": "Cluster"}, inplace=True
    )

    # Final Output columns
    final_cols = [
        "ASIN",
        "Cluster",
        "FC Replenishment Type",
        "Appointment Pending Qty",
        "Upcoming Shipment Qty",
        "Receiving + Intransit Qty",
        "Receiving Qty",
        "Intransit Qty",
        "Shipment ID",
        "DFC Next Arrival Date",
    ]
    report_df = report_df[final_cols]

    # Save output
    os.makedirs(os.path.dirname(output_file), exist_ok=True)
    print(f"Saving final report to {output_file}...")
    try:
        report_df.to_excel(output_file, index=False)
        print(
            f"Successfully generated final shipment report! Total rows: {len(report_df)}"
        )
    except Exception as e:
        print(f"Error saving output file: {e}")


if __name__ == "__main__":
    # Dynamic path resolution for standalone testing
    CURRENT_DIR = os.path.dirname(os.path.abspath(__file__))

    SHIPMENT_FILE = os.path.join(CURRENT_DIR, "Files", "Shipment 1.xlsx")
    MAPPING_FILE = os.path.join(CURRENT_DIR, "Files", "FC_Cluster_Mapping 3.xlsx")
    OUTPUT_FILE = os.path.join(CURRENT_DIR, "output", "Shipment_Report_Final.xlsx")

    if not os.path.exists(SHIPMENT_FILE):
        print(f"Please ensure {SHIPMENT_FILE} exists.")
    elif not os.path.exists(MAPPING_FILE):
        print(f"Please ensure {MAPPING_FILE} exists.")
    else:
        generate_shipment_report(SHIPMENT_FILE, MAPPING_FILE, OUTPUT_FILE)
