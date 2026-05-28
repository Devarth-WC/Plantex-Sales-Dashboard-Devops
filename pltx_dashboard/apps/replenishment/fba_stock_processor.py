import pandas as pd
import os

from .utils import read_tabular_file as load_data


def process_fba_stock(fba_file, mapping_file, output_file):
    print(f"Loading FBA stock from {fba_file}...")
    try:
        fba_df = load_data(fba_file)
    except Exception as e:
        print(f"Error reading FBA stock file: {e}")
        return

    print(f"Loading FC Mapping from {mapping_file}...")
    try:
        mapping_df = load_data(mapping_file)
    except Exception as e:
        print(f"Error reading mapping file: {e}")
        return

    # 1. Strip and uppercase column names to avoid whitespace and case issues
    fba_df.columns = fba_df.columns.astype(str).str.strip().str.upper()
    mapping_df.columns = mapping_df.columns.astype(str).str.strip().str.upper()

    # 2. Filter Disposition to only take "sellable" (case-insensitive)
    if "DISPOSITION" in fba_df.columns:
        fba_df = fba_df[
            fba_df["DISPOSITION"].astype(str).str.strip().str.lower() == "sellable"
        ]
    else:
        print("Warning: 'DISPOSITION' column not found in FBA stock data.")
        return

    # 3. Check for required FBA columns
    required_fba_cols = ["ASIN", "LOCATION", "DISPOSITION", "ENDING WAREHOUSE BALANCE"]
    for col in required_fba_cols:
        if col not in fba_df.columns:
            print(f"Error: Missing required column '{col}' in FBA stock file.")
            print(f"Available columns: {list(fba_df.columns)}")
            return

    # 4. Check for required Mapping columns
    required_mapping_cols = ["FC CODE", "CLUSTER NAME"]
    for col in required_mapping_cols:
        if col not in mapping_df.columns:
            print(f"Error: Missing required column '{col}' in Mapping file.")
            print(f"Available columns: {list(mapping_df.columns)}")
            return

    # Optional columns to keep
    keep_fba_cols = required_fba_cols.copy()
    # Amazon stock reports might use varying capitalization or spaces, check robustly if desired or just explicitly
    if "IN TRANSIT BETWEEN WAREHOUSES" in fba_df.columns:
        keep_fba_cols.append("IN TRANSIT BETWEEN WAREHOUSES")

    # Keep only required columns from both to optimize merge and memory
    fba_subset = fba_df[keep_fba_cols].copy()
    mapping_subset = mapping_df[required_mapping_cols].copy()

    # 5. Merge DataFrames
    # Match Location (from FBA file) with FC Code (from mapping file)
    print("Mapping Data...")
    merged_df = pd.merge(
        fba_subset, mapping_subset, left_on="LOCATION", right_on="FC CODE", how="left"
    )

    # 6. Final Output Format
    # Columns needed: ASIN, Cluster Name, Disposition, Ending warehouse balance
    # We will rename them back to the expected output format for compatibility
    rename_map = {
        "CLUSTER NAME": "Cluster Name",
        "DISPOSITION": "Disposition",
        "ENDING WAREHOUSE BALANCE": "Ending Warehouse Balance",
        "IN TRANSIT BETWEEN WAREHOUSES": "In Transit Between Warehouses"
    }
    merged_df.rename(columns=rename_map, inplace=True)
    
    final_cols = ["ASIN", "Cluster Name", "Disposition", "Ending Warehouse Balance"]
    if "In Transit Between Warehouses" in merged_df.columns:
        final_cols.append("In Transit Between Warehouses")
    final_df = merged_df[final_cols]

    # 7. Use ASIN as the main key - Ensure sorting or resetting index
    # We can sort by ASIN as the main key
    final_df = final_df.sort_values(by="ASIN").reset_index(drop=True)

    # Make output directory if not exists
    os.makedirs(os.path.dirname(output_file), exist_ok=True)

    print(f"Saving combined output to {output_file}...")
    try:
        final_df.to_excel(output_file, index=False)
        print(f"Successfully processed {len(final_df)} 'sellable' records!")
    except Exception as e:
        print(f"Error saving output file: {e}")


if __name__ == "__main__":
    # Dynamic path resolution for standalone testing
    CURRENT_DIR = os.path.dirname(os.path.abspath(__file__))

    FBA_STOCK_FILE = os.path.join(CURRENT_DIR, "Files", "FBA stock 1.xlsx")
    FC_MAPPING_FILE = os.path.join(CURRENT_DIR, "Files", "FC_Cluster_Mapping 3.xlsx")
    OUTPUT_FILE = os.path.join(CURRENT_DIR, "output", "stock_report_FBA.xlsx")

    if not os.path.exists(FBA_STOCK_FILE):
        print(f"Please ensure {FBA_STOCK_FILE} exists.")
    elif not os.path.exists(FC_MAPPING_FILE):
        print(f"Please ensure {FC_MAPPING_FILE} exists.")
    else:
        process_fba_stock(FBA_STOCK_FILE, FC_MAPPING_FILE, OUTPUT_FILE)
