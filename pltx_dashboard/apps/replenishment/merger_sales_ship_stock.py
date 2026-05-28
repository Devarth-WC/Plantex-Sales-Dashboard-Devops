import pandas as pd
import os

from .utils import read_tabular_file as load_data


def generate_master_report(
    sales_file,
    stock_file,
    shipment_file,
    product_details_file,
    business_report_file,
    database_file,
    raw_sales_file,
    pin_code_file,
    fc_mapping_file,
    input_sheet_file,
    output_file,
    flex_qty_file=None,
):
    print("Loading processed reports...")

    # --- Load Sales Data ---
    try:
        sales_df = load_data(sales_file)
        print(f" Loaded Sales Data: {len(sales_df)} rows")
    except Exception as e:
        print(f" Error reading sales file: {e}")
        sales_df = pd.DataFrame()

    # --- Load Stock Data ---
    try:
        stock_df = load_data(stock_file)
        print(f" Loaded Stock Data: {len(stock_df)} rows")
    except Exception as e:
        print(f" Error reading stock file: {e}")
        stock_df = pd.DataFrame()

    # --- Load Shipment Data ---
    try:
        ship_df = load_data(shipment_file)
        print(f" Loaded Shipment Data: {len(ship_df)} rows")
    except Exception as e:
        print(f" Error reading shipment file: {e}")
        ship_df = pd.DataFrame()

    # --- Load Product Details Data ---
    try:
        product_details_df = load_data(product_details_file)
        print(f" Loaded Product Details Data: {len(product_details_df)} rows")
    except Exception as e:
        print(f" Error reading product details file: {e}")
        product_details_df = pd.DataFrame()

    # --- Load Flex Qty Data (Optional) ---
    try:
        if flex_qty_file and os.path.exists(flex_qty_file):
            flex_qty_df = load_data(flex_qty_file)
            print(f" Loaded Flex Qty Data: {len(flex_qty_df)} rows")
        else:
            flex_qty_df = pd.DataFrame()
            print(" Flex Qty file not provided (optional)")
    except Exception as e:
        print(f" Error reading flex qty file: {e}")
        flex_qty_df = pd.DataFrame()

    # --- Load Business Report Data ---
    try:
        business_df = load_data(business_report_file)
        print(f" Loaded Business Report Data: {len(business_df)} rows")
    except Exception as e:
        print(f" Error reading business report file: {e}")
        business_df = pd.DataFrame()

    # --- Load Database (LIS) Data ---
    try:
        database_df = load_data(database_file)
        print(f" Loaded Database (LIS) Data: {len(database_df)} rows")
    except Exception as e:
        print(f" Error reading database file: {e}")
        database_df = pd.DataFrame()

    # --- Generate Ideal Cluster Mapping ---
    try:
        raw_sales_df = load_data(raw_sales_file)
        pin_code_df = load_data(pin_code_file)
        fc_mapping_df = load_data(fc_mapping_file)
        
        # Strip and uppercase column names for robustness
        raw_sales_df.columns = raw_sales_df.columns.astype(str).str.strip().str.upper()
        pin_code_df.columns = pin_code_df.columns.astype(str).str.strip().str.upper()
        fc_mapping_df.columns = fc_mapping_df.columns.astype(str).str.strip().str.upper()

        if "SHIPMENT TO POSTAL CODE" in raw_sales_df.columns:
            raw_sales_df["SHIPMENT TO POSTAL CODE"] = raw_sales_df[
                "SHIPMENT TO POSTAL CODE"
            ].astype(str)
        if "PIN CODE" in pin_code_df.columns:
            pin_code_df["PIN CODE"] = pin_code_df["PIN CODE"].astype(str)

        print(
            f" Loaded Raw Sales ({len(raw_sales_df)} rows), Pin Codes ({len(pin_code_df)} rows), and FC Mapping ({len(fc_mapping_df)} rows)"
        )

        # Clean postal codes
        postal_col = "SHIPMENT TO POSTAL CODE" if "SHIPMENT TO POSTAL CODE" in raw_sales_df.columns else "POSTAL CODE"
        raw_sales_df[postal_col] = (
            raw_sales_df[postal_col]
            .astype(str)
            .str.replace(r"\.0$", "", regex=True)
            .str.strip()
        )
        pin_code_df["PIN CODE"] = (
            pin_code_df["PIN CODE"].astype(str).str.replace(r"\.0$", "", regex=True).str.strip()
        )

        # Merge to get Ideal Cluster
        raw_sales_merged = pd.merge(
            raw_sales_df,
            pin_code_df[["PIN CODE", "FULFILMENT CLUSTER", "IDEAL CLUSTER"]],
            left_on=postal_col,
            right_on="PIN CODE",
            how="left",
        )

        # Group by ASIN and Fulfilment Cluster -> get Ideal Cluster
        asin_col = "ASIN" if "ASIN" in raw_sales_merged.columns else "CHILD ASIN"
        raw_sales_merged = raw_sales_merged.dropna(subset=[asin_col])
        raw_sales_merged[asin_col] = raw_sales_merged[asin_col].astype(str)
        ideal_mapping = (
            raw_sales_merged.groupby([asin_col, "FULFILMENT CLUSTER"])["IDEAL CLUSTER"]
            .first()
            .reset_index()
        )
        ideal_mapping.rename(
            columns={
                asin_col: "ASIN",
                "FULFILMENT CLUSTER": "Ideal Cluster",
                "IDEAL CLUSTER": "Ideal Cluster name",
            },
            inplace=True,
        )
        ideal_mapping["Ideal Cluster"] = ideal_mapping["Ideal Cluster"].astype(str)
    except Exception as e:
        print(f" Error generating Ideal Cluster mapping: {e}")
        ideal_mapping = pd.DataFrame(
            columns=["ASIN", "Ideal Cluster", "Ideal Cluster name"]
        )

    # --- Load Input Sheet Parameters ---
    print("\nLoading Input Sheet Parameters...")
    try:
        input_sheet_df = load_data(input_sheet_file)
        input_sheet_df.columns = input_sheet_df.columns.str.strip()

        def get_input_val(particular, default):
            try:
                val = input_sheet_df[input_sheet_df["Particular"] == particular][
                    "Value"
                ].values[0]
                return val
            except Exception:
                return default

        p0_days = int(str(get_input_val("P0 Demand DOC", 15)).split()[0])
        p1_days = int(str(get_input_val("P1 Demand DOC", 30)).split()[0])
        p2_days = int(str(get_input_val("P2 Demand DOC", 60)).split()[0])

        # Try to get Stock Report Date
        stock_date_val = get_input_val(
            "Stock Report Date", pd.Timestamp.now().strftime("%Y-%m-%d")
        )
        stock_report_date = pd.to_datetime(stock_date_val)
        print(
            f"  Parameters: P0={p0_days}, P1={p1_days}, P2={p2_days}, Stock Date={stock_report_date.date()}"
        )
    except Exception as e:
        print(f" Error loading input sheet: {e}. Using defaults.")
        p0_days, p1_days, p2_days = 15, 30, 60
        stock_report_date = pd.Timestamp.now()

    print("\nExtracting ASINs and Clusters...")
    asin_set = set()
    cluster_set = set()

    # Collect ASINs and clusters from actual report data
    for df, cluster_col in [
        (sales_df, "Cluster Name"),
        (stock_df, "Cluster Name"),
        (ship_df, "Cluster"),
    ]:
        if not df.empty:
            if "ASIN" in df.columns:
                asin_set.update(df["ASIN"].dropna().astype(str).unique())
            if cluster_col and cluster_col in df.columns:
                cluster_set.update(df[cluster_col].dropna().astype(str).unique())

    if not business_df.empty and "(Child) ASIN" in business_df.columns:
        asin_set.update(business_df["(Child) ASIN"].dropna().astype(str).unique())

    if not database_df.empty and "ASIN" in database_df.columns:
        asin_set.update(database_df["ASIN"].dropna().astype(str).unique())

    if not product_details_df.empty and "ASIN" in product_details_df.columns:
        asin_set.update(product_details_df["ASIN"].dropna().astype(str).unique())

    # Also include clusters from mapping files so every ASIN shows all clusters
    # (even if no sales/stock/shipment data exists for that ASIN+cluster combination)
    try:
        if 'pin_code_df' in locals() and not pin_code_df.empty:
            if "FULFILMENT CLUSTER" in pin_code_df.columns:
                cluster_set.update(pin_code_df["FULFILMENT CLUSTER"].dropna().astype(str).unique())
            if "IDEAL CLUSTER" in pin_code_df.columns:
                cluster_set.update(pin_code_df["IDEAL CLUSTER"].dropna().astype(str).unique())
        if 'fc_mapping_df' in locals() and not fc_mapping_df.empty:
            if "CLUSTER NAME" in fc_mapping_df.columns:
                cluster_set.update(fc_mapping_df["CLUSTER NAME"].dropna().astype(str).unique())
    except Exception as e:
        print(f" Warning: could not expand cluster list from mapping files: {e}")

    asin_list = sorted(list(asin_set))
    cluster_list = sorted([
        c for c in cluster_set
        if str(c).strip().lower() not in ('nan', 'none', '')
        and "_SF" not in str(c).upper()
    ])

    print(
        f" Found {len(asin_list)} unique ASINs and {len(cluster_list)} unique Clusters."
    )
    print(" Generating Master Cartesian Product...")

    # --- Create Cartesian Product (fast Pandas-native approach) ---
    # pd.MultiIndex.from_product is significantly faster than itertools.product
    # + list() because it builds the index natively without materialising a Python list.
    idx = pd.MultiIndex.from_product([asin_list, cluster_list], names=["ASIN", "Ideal Cluster"])
    master_df = idx.to_frame(index=False)

    # --- Merge Ideal Cluster name ---
    print("\nMerging Ideal Cluster Mapping...")
    master_df = pd.merge(
        master_df, ideal_mapping, on=["ASIN", "Ideal Cluster"], how="left"
    )
    master_df["Ideal Cluster name"] = master_df["Ideal Cluster name"].fillna("")

    # --- Merge Sales Data ---
    print("\nMerging Sales Data...")
    if not sales_df.empty:
        # Safely select columns that actually exist to avoid KeyError
        sales_cols_to_keep = ["ASIN", "Cluster Name", "DRR", "Sales Qty"]
        sales_existing_cols = [c for c in sales_cols_to_keep if c in sales_df.columns]
        
        sales_subset = sales_df[sales_existing_cols].copy()
        if "ASIN" in sales_subset.columns:
            sales_subset["ASIN"] = sales_subset["ASIN"].astype(str)
        if "Cluster Name" in sales_subset.columns:
            sales_subset.rename(columns={"Cluster Name": "Ideal Cluster"}, inplace=True)
            sales_subset["Ideal Cluster"] = sales_subset["Ideal Cluster"].astype(str)

        master_df = pd.merge(
            master_df, sales_subset, on=["ASIN", "Ideal Cluster"], how="left"
        )
    else:
        master_df["DRR"] = 0
        master_df["Sales Qty"] = 0

    # --- Map Zone based on Ideal Cluster ---
    print("Mapping Zone based on Ideal Cluster...")
    
    zone_mapping_dict = {}
    try:
        if 'fc_mapping_df' in locals() and not fc_mapping_df.empty:
            if "CLUSTER NAME" in fc_mapping_df.columns and "ZONE" in fc_mapping_df.columns:
                temp_df = fc_mapping_df.dropna(subset=["CLUSTER NAME", "ZONE"]).drop_duplicates(subset=["CLUSTER NAME"])
                zone_mapping_dict = dict(zip(temp_df["CLUSTER NAME"].astype(str).str.strip(), temp_df["ZONE"].astype(str).str.strip()))
    except Exception as e:
        print(f" Error extracting Zone mapping: {e}")

    if not zone_mapping_dict:
        zone_mapping_dict = {
            "CHN_CLUSTER": "CHENNAI",
            "NAG_CLUSTER": "NAGPUR",
            "GAA_CLUSTER": "GUWAHATI",
            "PAT_CLUSTER": "PATNA",
            "SAT_CLUSTER": "LUDHIANA",
            "PUN_CLUSTER": "PUNE",
            "AMD_CLUSTER": "AHMEDABAD",
            "HRA_CLUSTER": "DELHI",
            "BLR_CLUSTER": "BANGALORE",
            "IND_CLUSTER": "INDORE",
            "BOM_CLUSTER": "MUMBAI",
            "BHU_CLUSTER": "BHUBANESWAR",
            "JAI_CLUSTER": "JAIPUR",
            "COI_CLUSTER": "COIMBATORE",
            "HYD_CLUSTER": "HYDERABAD",
            "LKO_CLUSTER": "LUCKNOW",
            "KOL_CLUSTER": "KOLKATA",
            "HUB_CLUSTER": "HUBLI",
        }
    master_df["Zone"] = master_df["Ideal Cluster"].astype(str).str.strip().map(zone_mapping_dict).fillna("")

    # --- Merge Stock Data ---
    print("Merging Stock Data...")
    if not stock_df.empty:
        stock_df["ASIN"] = stock_df["ASIN"].astype(str)
        stock_df.rename(columns={"Cluster Name": "Ideal Cluster"}, inplace=True)
        stock_df["Ideal Cluster"] = stock_df["Ideal Cluster"].astype(str)
        # Group by in case multiple FCs belong to the same cluster mapping
        stock_cols = []
        if "Ending Warehouse Balance" in stock_df.columns:
            stock_cols.append("Ending Warehouse Balance")
        if "In Transit Between Warehouses" in stock_df.columns:
            stock_cols.append("In Transit Between Warehouses")
            
        if stock_cols:
            stock_agg = stock_df.groupby(["ASIN", "Ideal Cluster"], as_index=False)[
                stock_cols
            ].sum()
            if "Ending Warehouse Balance" in stock_agg.columns:
                stock_agg.rename(
                    columns={"Ending Warehouse Balance": "Stock Qty"}, inplace=True
                )
        if "In Transit Between Warehouses" in stock_agg.columns:
            stock_agg.rename(
                columns={"In Transit Between Warehouses": "Stock Transfer Qty"},
                inplace=True,
            )
        master_df = pd.merge(
            master_df, stock_agg, on=["ASIN", "Ideal Cluster"], how="left"
        )
    else:
        master_df["Stock Qty"] = 0

    # --- Merge Shipment Data ---
    print("Merging Shipment Data...")
    if not ship_df.empty:
        ship_df["ASIN"] = ship_df["ASIN"].astype(str)
        ship_df.rename(columns={"Cluster": "Ideal Cluster"}, inplace=True)
        ship_df["Ideal Cluster"] = ship_df["Ideal Cluster"].astype(str)

        # Split into DFC and IXD
        if "FC Replenishment Type" in ship_df.columns:
            dfc_df = ship_df[ship_df["FC Replenishment Type"] == "DFC"].copy()
            ixd_df = ship_df[ship_df["FC Replenishment Type"] == "IXD"].copy()
        else:
            dfc_df = ship_df.copy()
            ixd_df = pd.DataFrame(columns=ship_df.columns)

        # Rename DFC columns
        dfc_df.rename(
            columns={
                "Appointment Pending Qty": "DFC Appointment Pending Qty",
                "Upcoming Shipment Qty": "DFC Upcoming Shipment Qty",
                "Receiving + Intransit Qty": "DFC Receiving + Intransit Qty",
                "Receiving Qty": "DFC Receiving Qty",
                "Intransit Qty": "DFC Intransit Qty",
                "Shipment ID": "DFC Shipment ID",
                "DFC Next Arrival Date": "DFC Next Arrival Date",
            },
            inplace=True,
        )

        # Merge DFC
        dfc_cols = [
            "ASIN",
            "Ideal Cluster",
            "DFC Appointment Pending Qty",
            "DFC Upcoming Shipment Qty",
            "DFC Receiving + Intransit Qty",
            "DFC Receiving Qty",
            "DFC Intransit Qty",
            "DFC Shipment ID",
            "DFC Next Arrival Date",
        ]
        dfc_merge_subset = dfc_df[[c for c in dfc_cols if c in dfc_df.columns]]
        master_df = pd.merge(
            master_df, dfc_merge_subset, on=["ASIN", "Ideal Cluster"], how="left"
        )

        # Rename IXD columns
        ixd_df.rename(
            columns={
                "Appointment Pending Qty": "IXD Appointment Pending Qty",
                "Upcoming Shipment Qty": "IXD Upcoming Shipment Qty",
                "Receiving + Intransit Qty": "IXD Receiving + Intransit Qty",
                "Receiving Qty": "IXD Receiving Qty",
                "Intransit Qty": "IXD Intransit Qty",
                "Shipment ID": "IXD Shipment ID",
            },
            inplace=True,
        )

        # Merge IXD
        ixd_cols = [
            "ASIN",
            "Ideal Cluster",
            "IXD Appointment Pending Qty",
            "IXD Upcoming Shipment Qty",
            "IXD Receiving + Intransit Qty",
            "IXD Receiving Qty",
            "IXD Intransit Qty",
            "IXD Shipment ID",
        ]
        ixd_merge_subset = ixd_df[[c for c in ixd_cols if c in ixd_df.columns]]
        master_df = pd.merge(
            master_df, ixd_merge_subset, on=["ASIN", "Ideal Cluster"], how="left"
        )
    else:
        # Create missing columns
        shipment_empty_cols = [
            "DFC Appointment Pending Qty",
            "DFC Upcoming Shipment Qty",
            "DFC Receiving + Intransit Qty",
            "DFC Receiving Qty",
            "DFC Intransit Qty",
            "DFC Shipment ID",
            "DFC Next Arrival Date",
            "IXD Appointment Pending Qty",
            "IXD Upcoming Shipment Qty",
            "IXD Receiving + Intransit Qty",
            "IXD Receiving Qty",
            "IXD Intransit Qty",
            "IXD Shipment ID",
        ]
        for col in shipment_empty_cols:
            master_df[col] = 0

    # --- Merge Product Details Data ---
    print("Merging Product Details Data...")
    if not product_details_df.empty:
        product_details_df["ASIN"] = product_details_df["ASIN"].astype(str)
        prod_cols = [
            "ASIN",
            "SKU",
            "HSN CODE",
            "VENDOR NAME",
            "PRODUCTS STATUS",
            "ACT WEIGHT",
            "VOLUMETRIC WEIGHT",
            "PRODUCT TYPE",
            "PRODUCT SIZE",
            "Portfolio",
            "Category",
        ]
        
        # Extract Brand if available
        if "Brand" in product_details_df.columns:
            prod_cols.append("Brand")
        
        prod_subset = product_details_df[
            [c for c in prod_cols if c in product_details_df.columns]
        ].drop_duplicates(subset=["ASIN"])
        master_df = pd.merge(master_df, prod_subset, on="ASIN", how="left")
    else:
        for col in [
            "SKU",
            "HSN CODE",
            "VENDOR NAME",
            "PRODUCTS STATUS",
            "ACT WEIGHT",
            "VOLUMETRIC WEIGHT",
            "PRODUCT TYPE",
            "PRODUCT SIZE",
            "Portfolio",
            "Category",
        ]:
            master_df[col] = ""
        master_df["Brand"] = ""

    # Ensure Brand column exists
    if "Brand" not in master_df.columns:
        master_df["Brand"] = ""

    # --- Merge Flex Qty Data ---
    print("Merging Flex Qty Data...")
    if not flex_qty_df.empty:
        try:
            # Ensure required columns exist in Flex Qty file
            required_flex_cols = ["ASIN", "Cluster", "Qty"]
            flex_cols_available = [c for c in required_flex_cols if c in flex_qty_df.columns]
            
            if len(flex_cols_available) >= 2:  # At least need ASIN and Cluster or Qty
                # Rename columns to match master_df naming
                flex_qty_df_renamed = flex_qty_df.copy()
                if "ASIN" in flex_qty_df_renamed.columns:
                    flex_qty_df_renamed["ASIN"] = flex_qty_df_renamed["ASIN"].astype(str)
                if "Cluster" in flex_qty_df_renamed.columns:
                    flex_qty_df_renamed["Ideal Cluster"] = flex_qty_df_renamed["Cluster"].astype(str)
                
                # Rename Qty to Flex Qty
                if "Qty" in flex_qty_df_renamed.columns:
                    flex_qty_df_renamed.rename(columns={"Qty": "Flex Qty"}, inplace=True)
                
                # Keep only necessary columns
                flex_merge_cols = [c for c in ["ASIN", "Ideal Cluster", "Flex Qty"] if c in flex_qty_df_renamed.columns]
                flex_qty_subset = flex_qty_df_renamed[flex_merge_cols].drop_duplicates(
                    subset=["ASIN", "Ideal Cluster"] if "Ideal Cluster" in flex_merge_cols else ["ASIN"]
                )
                
                master_df = pd.merge(
                    master_df,
                    flex_qty_subset,
                    on=[c for c in ["ASIN", "Ideal Cluster"] if c in flex_merge_cols],
                    how="left"
                )
            else:
                print(f" Warning: Flex Qty file missing required columns. Found: {flex_cols_available}")
                master_df["Flex Qty"] = ""
        except Exception as e:
            print(f" Error merging Flex Qty data: {e}")
            master_df["Flex Qty"] = ""
    else:
        master_df["Flex Qty"] = ""

    # --- Merge Business Report Data ---
    print("Merging Business Report Data...")
    if not business_df.empty:
        business_df["(Child) ASIN"] = business_df["(Child) ASIN"].astype(str)
        # Clean currency format in Ordered Product Sales before merging
        if "Ordered Product Sales" in business_df.columns:
            business_df["Ordered Product Sales"] = business_df["Ordered Product Sales"].apply(
                lambda x: str(x).replace("₹", "").replace(",", "").strip() if isinstance(x, str) else x
            )
        business_cols = [
            "(Child) ASIN",
            "Page Views - Total",
            "Units Ordered",
            "Ordered Product Sales",
            "Total Order Items",
        ]
        b_subset = (
            business_df[[c for c in business_cols if c in business_df.columns]]
            .drop_duplicates(subset=["(Child) ASIN"])
            .copy()
        )

        # We need to map this to master_df by ASIN.
        # master_df['ASIN'] == business_df['(Child) ASIN']
        # We keep '(Child) ASIN' in b_subset, but create 'ASIN' for joining
        b_subset["ASIN"] = b_subset["(Child) ASIN"]
        # Ensure Ordered Product Sales is numeric before merging
        if "Ordered Product Sales" in b_subset.columns:
            b_subset["Ordered Product Sales"] = pd.to_numeric(b_subset["Ordered Product Sales"], errors="coerce").fillna(0)
        master_df = pd.merge(master_df, b_subset, on="ASIN", how="left")
    else:
        for col in [
            "(Child) ASIN",
            "Page Views - Total",
            "Units Ordered",
            "Ordered Product Sales",
            "Total Order Items",
        ]:
            master_df[col] = ""

    # --- Merge Database (LIS) Data ---
    print("Merging Database (LIS) Data...")
    if not database_df.empty:
        database_df["ASIN"] = database_df["ASIN"].astype(str)
        if "Cluster" in database_df.columns:
            database_df["Cluster"] = database_df["Cluster"].astype(str)

        lis_mapping = {
            "CHN_CLUSTER": "CHENNAI",
            "NAG_CLUSTER": "NAGPUR",
            "GAA_CLUSTER": "GUWAHATI",
            "PAT_CLUSTER": "PATNA",
            "SAT_CLUSTER": "LUDHIANA",
            "PUN_CLUSTER": "PUNE",
            "AMD_CLUSTER": "AHMEDABAD",
            "HRA_CLUSTER": "DELHI",
            "BLR_CLUSTER": "BANGALORE",
            "IND_CLUSTER": "INDORE",
            "BOM_CLUSTER": "MUMBAI",
            "BHU_CLUSTER": "BHUBANESWAR",
            "JAI_CLUSTER": "JAIPUR",
            "COI_CLUSTER": "COIMBATORE",
            "HYD_CLUSTER": "HYDERABAD",
            "LKO_CLUSTER": "LUCKNOW",
            "KOL_CLUSTER": "KOLKATA",
            "HUB_CLUSTER": "HUBLI",
        }

        # Determine the LIS Cluster Name first!
        # Map LIS_CLUSTER based exclusively on the Ideal Cluster column
        master_df["LIS_CLUSTER"] = (
            master_df["Ideal Cluster"].map(lis_mapping).fillna("")
        )

        db_cols = [
            "ASIN",
            "Cluster",
            "Sum of Local Shipped Units",
            "Sum of Total Units",
        ]
        db_subset = database_df[[c for c in db_cols if c in database_df.columns]].copy()

        db_subset.rename(
            columns={
                "Cluster": "LIS_CLUSTER",
                "Sum of Local Shipped Units": "LIS_LOCAL_QTY",
                "Sum of Total Units": "LIS_TOTAL_QTY",
            },
            inplace=True,
        )

        db_group_cols = ["ASIN"]
        if "LIS_CLUSTER" in db_subset.columns:
            db_group_cols.append("LIS_CLUSTER")
            
        db_agg_cols = [c for c in ["LIS_LOCAL_QTY", "LIS_TOTAL_QTY"] if c in db_subset.columns]
        
        if len(db_group_cols) > 0 and len(db_agg_cols) > 0:
            db_agg = db_subset.groupby(db_group_cols, as_index=False)[db_agg_cols].sum()
        else:
            db_agg = db_subset.copy()

        # Merge database data onto master_df using ASIN and LIS_CLUSTER
        # Ensure data types are consistent for merge
        merge_on_cols = ["ASIN"]
        if "LIS_CLUSTER" in db_agg.columns and "LIS_CLUSTER" in master_df.columns:
            db_agg["LIS_CLUSTER"] = db_agg["LIS_CLUSTER"].astype(str)
            master_df["LIS_CLUSTER"] = master_df["LIS_CLUSTER"].astype(str)
            merge_on_cols.append("LIS_CLUSTER")
            
        master_df = pd.merge(master_df, db_agg, on=merge_on_cols, how="left")

    else:
        master_df["LIS_LOCAL_QTY"] = 0
        master_df["LIS_TOTAL_QTY"] = 0
        master_df["LIS_CLUSTER"] = ""

    # --- Add BSR ---
    master_df["BSR"] = ""

    # --- Fill NaNs ---
    print("\nData Cleanup & Calculations...")
    
    # Clean 'Ordered Product Sales' column to handle currency format (₹10,11,873.00 -> 1011873.00)
    if "Ordered Product Sales" in master_df.columns:
        master_df["Ordered Product Sales"] = master_df["Ordered Product Sales"].apply(
            lambda x: str(x).replace("₹", "").replace(",", "").strip() if isinstance(x, str) else x
        )
    
    numeric_columns = [
        "DRR",
        "Sales Qty",
        "Stock Qty",
        "Stock Transfer Qty",
        "Receiving Qty",
        "Flex Qty",
        "DFC Appointment Pending Qty",
        "DFC Upcoming Shipment Qty",
        "DFC Receiving + Intransit Qty",
        "DFC Receiving Qty",
        "DFC Intransit Qty",
        "IXD Appointment Pending Qty",
        "IXD Upcoming Shipment Qty",
        "IXD Receiving + Intransit Qty",
        "IXD Receiving Qty",
        "IXD Intransit Qty",
        "LIS_LOCAL_QTY",
        "LIS_TOTAL_QTY",
        "Page Views - Total",
        "Units Ordered",
        "Ordered Product Sales",
        "Total Order Items",
        "National Doc",
    ]

    for col in numeric_columns:
        if col in master_df.columns:
            master_df[col] = (
                pd.to_numeric(master_df[col], errors="coerce").fillna(0).round(2)
            )

    # Fill text NaNs
    for col in ["DFC Shipment ID", "IXD Shipment ID", "DFC Next Arrival Date"]:
        if col in master_df.columns:
            master_df[col] = master_df[col].fillna("")

    # --- Calculate Total Shipment Qty ---
    # As per requirements, Shipment Qty is the sum of all 3 DFC categories.
    master_df["Shipment Qty"] = (
        master_df.get("DFC Appointment Pending Qty", 0)
        + master_df.get("DFC Upcoming Shipment Qty", 0)
        + master_df.get("DFC Intransit Qty", 0)
    )

    # --- Add Conversion Pct ---
    print("Calculating Conversion Pct...")

    # Calculate Receiving Qty (DFC only, not IXD as per requirements)
    master_df["Receiving Qty"] = master_df.get("DFC Receiving Qty", 0)

    # Stock Transfer Qty comes strictly from Stock Report ('In Transit Between Warehouses')
    master_df["Stock Transfer Qty"] = master_df.get(
        "Stock Transfer Qty", 0
    )  # if it already existed from stock_df merge

    total_orders = master_df.get("Total Order Items", pd.Series([0]*len(master_df)))
    page_views = master_df.get("Page Views - Total", pd.Series([0]*len(master_df)))
    
    # Handle scalar fallback from .get()
    if isinstance(total_orders, (int, float)):
        total_orders = pd.Series([total_orders]*len(master_df))
    if isinstance(page_views, (int, float)):
        page_views = pd.Series([page_views]*len(master_df))
        
    master_df["Conversion Pct"] = (
        (total_orders / page_views.replace(0, pd.NA)) * 100
    ).replace([float("inf"), -float("inf")], 0).fillna(0).round(2).astype(str) + "%"

    # --- Calculate P0, P1, P2 ---
    master_df["P0"] = (
        (
            master_df["DRR"] * p0_days
            - master_df["Stock Qty"]
            - master_df["Receiving Qty"]
            - master_df["Stock Transfer Qty"]
            - master_df["Shipment Qty"]
        )
        .clip(lower=0)
        .round(2)
    )
    master_df["P1"] = (
        (
            master_df["DRR"] * p1_days
            - master_df["Stock Qty"]
            - master_df["Receiving Qty"]
            - master_df["Stock Transfer Qty"]
            - master_df["Shipment Qty"]
            - master_df["P0"]
        )
        .clip(lower=0)
        .round(2)
    )
    master_df["P2"] = (
        (
            master_df["DRR"] * p2_days
            - master_df["Stock Qty"]
            - master_df["Receiving Qty"]
            - master_df["Stock Transfer Qty"]
            - master_df["Shipment Qty"]
            - master_df["P1"]
            - master_df["P0"]
        )
        .clip(lower=0)
        .round(2)
    )

    # --- Intelligence Features ---
    print("Calculating Intelligence Features...")
    import numpy as np

    today = pd.Timestamp.now().normalize()

    # Days Diff between today and stock report date

    # Days of Cover
    master_df["Days of Cover"] = (
        (master_df["Stock Qty"] / master_df["DRR"])
        .replace([np.inf, -np.inf], 999)
        .fillna(0)
    )

    # --- National Doc (National Days of Cover) ---
    # Aggregate Stock Qty and DRR at ASIN level (across all clusters)
    print("Calculating National Doc...")
    national_agg = master_df.groupby("ASIN", as_index=False).agg(
        National_Stock_Qty=("Stock Qty", "sum"),
        National_DRR=("DRR", "sum"),
    )
    national_agg["National Doc"] = (
        (national_agg["National_Stock_Qty"] / national_agg["National_DRR"])
        .replace([np.inf, -np.inf], 0)
        .fillna(0)
        .round(2)
    )
    master_df = pd.merge(
        master_df,
        national_agg[["ASIN", "National Doc"]],
        on="ASIN",
        how="left",
    )
    master_df["National Doc"] = master_df["National Doc"].fillna(0)

    # Stock Out Date Calculation
    days_stock_recv = (
        ((master_df["Stock Qty"] + master_df["Receiving Qty"]) / master_df["DRR"])
        .replace([np.inf, -np.inf], 999)
        .fillna(999)
    )
    days_all = (
        (
            (
                master_df["Stock Qty"]
                + master_df["Receiving Qty"]
                + master_df["Shipment Qty"]
            )
            / master_df["DRR"]
        )
        .replace([np.inf, -np.inf], 999)
        .fillna(999)
    )

    expected_out_date_dt = today + pd.to_timedelta(days_stock_recv, unit="D")
    
    # Handle DFC Next Arrival Date cleanly whether it's a column, missing, or scalar
    arrival_col = master_df.get("DFC Next Arrival Date", pd.Series([pd.NaT]*len(master_df)))
    if isinstance(arrival_col, str):
        arrival_col = pd.Series([arrival_col]*len(master_df))
    shipment_date_dt = pd.to_datetime(arrival_col, errors="coerce")

    # Use pd.notna instead of .notna() to handle both Series and scalar NaT
    condition = pd.notna(shipment_date_dt) & (expected_out_date_dt > shipment_date_dt)
    final_days = np.where(condition, days_all, days_stock_recv)

    # --- Vectorized Stock Out Date ---
    # Rows with DRR==0 or days>=999 → "Never", rest → date string
    never_mask = pd.isna(pd.Series(final_days)) | (pd.Series(final_days) >= 999)
    final_days_series = pd.Series(final_days).clip(upper=998)  # avoid huge timedelta
    out_dates = (today + pd.to_timedelta(final_days_series.round().astype(int), unit="D")).dt.strftime("%Y-%m-%d")
    master_df["Stock Out Date"] = out_dates.where(~never_mask, "Never")

    # --- Vectorized Stock Cover Alert ---
    doc = master_df["Days of Cover"]
    master_df["Stock cover alert"] = pd.cut(
        doc,
        bins=[-float("inf"), 30, 45, 60, 90, 120, float("inf")],
        labels=["<30 Days", "<45 Days", "Healthy", ">60 Days", ">90 Days", ">120 Days"],
        right=False,
    ).astype(str).replace("nan", "")

    # --- Finalize Dataframe Layout ---
    final_columns = [
        "BSR",
        "ASIN",
        "Ideal Cluster",
        "SKU",
        "HSN CODE",
        "VENDOR NAME",
        "PRODUCTS STATUS",
        "ACT WEIGHT",
        "VOLUMETRIC WEIGHT",
        "PRODUCT TYPE",
        "PRODUCT SIZE",
        "Portfolio",
        "Category",
        "Brand",
        "Zone",
        "LIS_CLUSTER",
        "Flex Qty",
        "National Doc",
        "DRR",
        "Sales Qty",
        "Stock Qty",
        "Receiving Qty",
        "Stock Transfer Qty",
        "Shipment Qty",
        "P0",
        "P1",
        "P2",
        "Stock Out Date",
        "Stock cover alert",
        "Page Views - Total",
        "Units Ordered",
        "Total Order Items",
        "Conversion Pct",
        "Ordered Product Sales",
        "LIS_LOCAL_QTY",
        "LIS_TOTAL_QTY",
        "DFC Appointment Pending Qty",
        "DFC Upcoming Shipment Qty",
        "DFC Intransit Qty",
        "DFC Shipment ID",
        "DFC Next Arrival Date",
        "IXD Appointment Pending Qty",
        "IXD Upcoming Shipment Qty",
        "IXD Receiving + Intransit Qty",
        "IXD Shipment ID",
    ]

    # No additional renaming needed after restructuring

    # Reorder filtering only those that exist (to prevent key errors)
    ordered_cols = [col for col in final_columns if col in master_df.columns]
    master_df = master_df[ordered_cols]

    # --- Save Output ---
    os.makedirs(os.path.dirname(output_file), exist_ok=True)
    print(f"\nSaving Final Master Report to {output_file}...")
    master_df.to_csv(output_file, index=False)
    print(
        f"\n✅ Successfully generated Final Master Report with {len(master_df)} records!"
    )


if __name__ == "__main__":
    # Dynamic path resolution for standalone testing
    CURRENT_DIR = os.path.dirname(os.path.abspath(__file__))

    # Input file locations (outputs of the previous processors)
    SALES_FILE = os.path.join(CURRENT_DIR, "output", "Final_Sales_Report.csv")
    STOCK_FILE = os.path.join(CURRENT_DIR, "output", "FBA_Mapped_Output.xlsx")
    SHIPMENT_FILE = os.path.join(CURRENT_DIR, "output", "Shipment_Report_Final.xlsx")
    PRODUCT_DETAILS_FILE = os.path.join(CURRENT_DIR, "Files", "Product Details 1.xlsx")
    BUSINESS_REPORT_FILE = os.path.join(
        CURRENT_DIR, "Files", "BusinessReport-02-04-26.csv"
    )
    DATABASE_FILE = os.path.join(CURRENT_DIR, "Files", "Database.xlsx")
    RAW_SALES_FILE = os.path.join(CURRENT_DIR, "Files", "Sales 1.csv")
    PIN_CODE_FILE = os.path.join(CURRENT_DIR, "Files", "PIN CODE and CLUSTER 1.csv")
    INPUT_SHEET_FILE = os.path.join(CURRENT_DIR, "Files", "Input Sheet 1.xlsx")

    # Final output file location
    OUTPUT_FILE = os.path.join(CURRENT_DIR, "output", "Master_Merged_Report.csv")
    FC_MAPPING_FILE = os.path.join(CURRENT_DIR, "Files", "FC_Cluster_Mapping 3.xlsx")

    generate_master_report(
        SALES_FILE,
        STOCK_FILE,
        SHIPMENT_FILE,
        PRODUCT_DETAILS_FILE,
        BUSINESS_REPORT_FILE,
        DATABASE_FILE,
        RAW_SALES_FILE,
        PIN_CODE_FILE,
        FC_MAPPING_FILE,
        INPUT_SHEET_FILE,
        OUTPUT_FILE,
    )
