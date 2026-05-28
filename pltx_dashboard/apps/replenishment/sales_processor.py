import os
import pandas as pd

try:
    from .utils import read_tabular_file
except ImportError:  # pragma: no cover - fallback for standalone script execution
    from utils import read_tabular_file


def process_sales_report(sales_file, pin_file, output_file):
    output_dir = os.path.dirname(output_file)
    if output_dir:
        os.makedirs(output_dir, exist_ok=True)

    if not os.path.exists(sales_file) or not os.path.exists(pin_file):
        print("Error: Required files not found.")
        return None

    print("Loading datasets...")
    # Load the datasets, treating postal codes as strings
    df_sales = read_tabular_file(sales_file, dtype={"Shipment To Postal Code": str})
    df_pin = read_tabular_file(pin_file, dtype={"PIN CODE": str})

    # Strip and uppercase column names for robustness
    df_sales.columns = df_sales.columns.astype(str).str.strip().str.upper()
    df_pin.columns = df_pin.columns.astype(str).str.strip().str.upper()

    print("Processing Dates and Numerical Columns...")
    # Date parsing: Separate into proper date and time, ignoring time
    # The format is like 2026-03-14T23:59:48+05:30
    date_col = "CUSTOMER SHIPMENT DATE"
    if date_col not in df_sales.columns:
        # Fallback for different possible names
        for col in df_sales.columns:
            if "SHIPMENT DATE" in col or "DATE" in col:
                date_col = col
                break

    date_time_split = (
        df_sales[date_col].astype(str).str.split("T", n=1, expand=True)
    )

    # Overwrite with only the proper Date part (ignoring time string)
    df_sales["CUSTOMER SHIPMENT DATE"] = pd.to_datetime(
        date_time_split[0], errors="coerce"
    )

    # Calculate Total from amounts
    for col in ["PRODUCT AMOUNT", "SHIPPING AMOUNT", "GIFT AMOUNT"]:
        if col in df_sales.columns:
            df_sales[col] = pd.to_numeric(df_sales[col], errors="coerce").fillna(0)

    df_sales["TOTAL"] = (
        df_sales.get("PRODUCT AMOUNT", 0)
        + df_sales.get("SHIPPING AMOUNT", 0)
        + df_sales.get("GIFT AMOUNT", 0)
    )

    print("Preparing Pincodes for Merge...")
    # Clean '.0' trailing decimals from zip codes if they were inferred as floats originally
    postal_col = "SHIPMENT TO POSTAL CODE" if "SHIPMENT TO POSTAL CODE" in df_sales.columns else "POSTAL CODE"
    df_sales[postal_col] = (
        df_sales[postal_col]
        .astype(str)
        .str.replace(r"\.0$", "", regex=True)
        .str.strip()
    )
    df_pin["PIN CODE"] = (
        df_pin["PIN CODE"].astype(str).str.replace(r"\.0$", "", regex=True).str.strip()
    )

    # De-duplicate PIN file to ensure a clean 1:1 map
    df_pin_unique = df_pin.drop_duplicates(subset=["PIN CODE"])

    print("Merging Sales data with PIN mappings...")
    # Perform Left Join mapping Shipment To Postal Code to PIN CODE
    df_merged = pd.merge(
        df_sales,
        df_pin_unique[["PIN CODE", "FULFILMENT CLUSTER", "ZONE"]],
        left_on=postal_col,
        right_on="PIN CODE",
        how="left",
    )

    # Handle Missing mappings
    df_merged["FULFILMENT CLUSTER"] = df_merged["FULFILMENT CLUSTER"].fillna("N/A")
    df_merged["ZONE"] = df_merged["ZONE"].fillna("N/A")

    print("Calculating file-level Sales Day Count...")
    # File-level: find max and min of Customer Shipment Date across the entire file
    file_max_date = df_merged["CUSTOMER SHIPMENT DATE"].max()
    file_min_date = df_merged["CUSTOMER SHIPMENT DATE"].min()
    file_sales_day_count = max(
        (file_max_date - file_min_date).days, 1
    )  # At least 1 day
    print(
        f"  Date range: {file_min_date} to {file_max_date} = {file_sales_day_count} days"
    )

    print("Grouping Data by ASIN and Cluster Name...")
    # Group by ASIN and Cluster
    asin_col = "ASIN" if "ASIN" in df_merged.columns else "CHILD ASIN"
    grouped = df_merged.groupby([asin_col, "FULFILMENT CLUSTER"])

    # Using Named Aggregation for clarity
    qty_col = "QUANTITY" if "QUANTITY" in df_merged.columns else "QTY"
    order_col = "AMAZON ORDER ID" if "AMAZON ORDER ID" in df_merged.columns else "ORDER ID"
    
    df_result = grouped.agg(
        Sales_Qty=(qty_col, "sum"),
        Unique_Order_Count=(order_col, "nunique"),
        Unique_Pincode_Count=(postal_col, "nunique"),
        Total_Sum=("TOTAL", "sum"),
        Demand_Zone=("ZONE", "first"),
    ).reset_index()

    print("Calculating metrics...")
    # Rename cluster
    df_result.rename(columns={asin_col: "ASIN", "FULFILMENT CLUSTER": "Cluster Name"}, inplace=True)

    # Sales Day Count = (max date - min date) across the entire file, applied to every row
    df_result["Sales Day Count"] = file_sales_day_count

    # DRR
    df_result["DRR"] = df_result["Sales_Qty"] / df_result["Sales Day Count"]
    df_result["DRR"] = df_result["DRR"].round(2)
    df_result["Total"] = df_result["Total_Sum"].round(2)

    # Final Column Selection
    final_columns = [
        "ASIN",
        "Cluster Name",
        "Sales Qty",
        "Sales Day Count",
        "DRR",
        "Total",
        "Unique Order Count",
        "Unique Pincode Count",
        "Demand Zone",
    ]

    # Rename columns to match final expected output
    rename_mapping = {
        "Sales_Qty": "Sales Qty",
        "Unique_Order_Count": "Unique Order Count",
        "Unique_Pincode_Count": "Unique Pincode Count",
        "Demand_Zone": "Demand Zone",
    }
    df_result.rename(columns=rename_mapping, inplace=True)

    df_final = df_result[final_columns]

    print("Saving Final Report...")
    df_final.to_csv(output_file, index=False)
    print(f"Successfully saved {len(df_final)} clustered records to {output_file}")

    return df_final


if __name__ == "__main__":
    # Dynamic path resolution for standalone testing
    CURRENT_DIR = os.path.dirname(os.path.abspath(__file__))

    process_sales_report(
        sales_file=os.path.join(CURRENT_DIR, "Files", "Sales 1.csv"),
        pin_file=os.path.join(CURRENT_DIR, "Files", "PIN CODE and CLUSTER 1.csv"),
        output_file=os.path.join(CURRENT_DIR, "output", "Final_Sales_Report.csv"),
    )
