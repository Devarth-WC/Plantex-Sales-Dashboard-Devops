import os
import pandas as pd
from celery import shared_task

# Logic imports
from .validation import validate_sales, validate_shipment, validate_stock, validate_lis
from .sales_processor import process_sales_report
from .shipment_processor import generate_shipment_report
from .fba_stock_processor import process_fba_stock
from .merger_sales_ship_stock import generate_master_report


@shared_task(bind=True)
def validate_reports_celery(self, reports_to_validate, mapping_files):
    """
    Celery task for report validation.

    Parameters
    ----------
    reports_to_validate : list of (report_type, file_path) tuples
    mapping_files : dict with keys 'FC_Cluster', 'Pincode_Cluster', 'Assortment', 'Input_Sheet'
        File paths to the mapping files used to generate master data.
    """
    results = {}
    total_errors = 0
    error_data_map = {}

    try:
        # Generate master data inside the Celery task (was previously blocking the view)
        from .utils import generate_master_data

        master_data = generate_master_data(
            mapping_files.get("FC_Cluster"),
            mapping_files.get("Pincode_Cluster"),
            mapping_files.get("Assortment"),
            mapping_files.get("Input_Sheet"),
        )

        for r_type, path in reports_to_validate:
            results[r_type] = {"error_count": 0, "has_errors": False}

            if path and os.path.exists(path):
                if r_type == "Sales":
                    errs = validate_sales(path, master_data)
                elif r_type == "Shipment":
                    errs = validate_shipment(path, master_data)
                elif r_type == "Stock":
                    errs = validate_stock(path, master_data)
                elif r_type == "LIS":
                    errs = validate_lis(path, master_data)
                else:
                    errs = []

                error_count = len(errs)
                results[r_type]["error_count"] = error_count
                total_errors += error_count

                if error_count > 0:
                    df_errors = pd.DataFrame(errs)
                    cols = ["Row", "Column", "Value", "Message", "Shipment_id", "ASIN"]
                    df_errors = df_errors[[c for c in cols if c in df_errors.columns]]

                    error_records = df_errors.to_dict("records")
                    error_data_map[r_type] = {
                        "data": error_records,
                        "columns": df_errors.columns.tolist(),
                    }
                    results[r_type]["has_errors"] = True

        return {
            "status": "success",
            "total_errors": total_errors,
            "reports": results,
            "error_data_map": error_data_map,
            "task_type": "validation",
        }
    except Exception as e:
        return {"status": "error", "message": str(e), "task_type": "validation"}


@shared_task(bind=True)
def generate_master_celery(self, files, temp_dir):
    try:
        sales_out = os.path.join(temp_dir, "Final_Sales_Report.csv")
        stock_out = os.path.join(temp_dir, "FBA_Mapped_Output.xlsx")
        shipment_out = os.path.join(temp_dir, "Shipment_Report_Final.xlsx")

        process_sales_report(files["Sales"], files["Pincode_Cluster"], sales_out)
        process_fba_stock(files["Stock"], files["FC_Cluster"], stock_out)
        generate_shipment_report(files["Shipment"], files["FC_Cluster"], shipment_out)

        master_out_csv = os.path.join(temp_dir, "Master_Merged_Report.csv")
        
        # Get Flex Qty file if provided (optional)
        flex_qty_file = files.get("Flex_Qty")
        if flex_qty_file and not os.path.exists(flex_qty_file):
            flex_qty_file = None
        
        generate_master_report(
            sales_out,
            stock_out,
            shipment_out,
            files["Assortment"],
            files["Business_Report"],
            files["LIS"],
            files["Sales"],
            files["Pincode_Cluster"],
            files["FC_Cluster"],
            files["Input_Sheet"],
            master_out_csv,
            flex_qty_file=flex_qty_file,
        )

        master_out_excel = os.path.join(temp_dir, "Master_Merged_Report.xlsx")
        try:
            df_master = pd.read_csv(master_out_csv, low_memory=False)
            annexure_data = [
                {
                    "Header Name": "BSR",
                    "Description": "Best Sellers Rank — indicates how well the product sells relative to others in its Amazon category. Lower rank = higher sales.",
                    "Source": "Reserved / Manual Entry",
                    "Calculation / Logic": "Placeholder column for future BSR data integration. Currently left blank.",
                },
                {
                    "Header Name": "ASIN",
                    "Description": "Amazon Standard Identification Number — a unique 10-character alphanumeric identifier assigned by Amazon to every product listing.",
                    "Source": "All uploaded files (Sales, Stock, Shipment, Product Details, Business Report, LIS)",
                    "Calculation / Logic": "Union of all unique ASINs found across Sales, Stock, Shipment, Product Details, Business Report, and LIS files. Forms the row key along with Ideal Cluster.",
                },
                {
                    "Header Name": "Ideal Cluster",
                    "Description": "The optimized fulfillment cluster (e.g., BLR_CLUSTER, HYD_CLUSTER) mapped for each ASIN based on sales postal codes and pin-code-to-cluster mapping.",
                    "Source": "Sales Report + Pin Code Cluster Mapping",
                    "Calculation / Logic": "Raw Sales postal codes are matched against Pin Code Cluster file to determine the Fulfilment Cluster for each order. All unique clusters form the column key. Each row in the report is one ASIN × Ideal Cluster combination (Cartesian product).",
                },
                {
                    "Header Name": "SKU",
                    "Description": "Stock Keeping Unit — the internal product code used by the seller for inventory tracking.",
                    "Source": "Product Details / Assortment Master file",
                    "Calculation / Logic": "Direct lookup from Product Details file by matching ASIN. Deduplicated to one SKU per ASIN.",
                },
                {
                    "Header Name": "HSN CODE",
                    "Description": "Harmonized System of Nomenclature code — a standardized tax classification code for the product used in GST filing.",
                    "Source": "Product Details / Assortment Master file",
                    "Calculation / Logic": "Direct lookup from Product Details file by matching ASIN.",
                },
                {
                    "Header Name": "VENDOR NAME",
                    "Description": "Name of the supplier or vendor who provides this product.",
                    "Source": "Product Details / Assortment Master file",
                    "Calculation / Logic": "Direct lookup from Product Details file by matching ASIN.",
                },
                {
                    "Header Name": "PRODUCTS STATUS",
                    "Description": "Current operational status of the product (e.g., Active, Discontinued, Blocked). Helps identify products that should or should not be replenished.",
                    "Source": "Product Details / Assortment Master file",
                    "Calculation / Logic": "Direct lookup from Product Details file by matching ASIN.",
                },
                {
                    "Header Name": "ACT WEIGHT",
                    "Description": "Actual physical weight of the product in kilograms. Used for shipping cost calculations.",
                    "Source": "Product Details / Assortment Master file",
                    "Calculation / Logic": "Direct lookup from Product Details file by matching ASIN.",
                },
                {
                    "Header Name": "VOLUMETRIC WEIGHT",
                    "Description": "Volumetric (dimensional) weight calculated from the product's physical dimensions (L × W × H / 5000). Courier charges use the higher of actual vs volumetric weight.",
                    "Source": "Product Details / Assortment Master file",
                    "Calculation / Logic": "Direct lookup from Product Details file by matching ASIN.",
                },
                {
                    "Header Name": "PRODUCT TYPE",
                    "Description": "Classification type of the product (e.g., Standard, Oversize). Affects fulfillment method and warehouse handling.",
                    "Source": "Product Details / Assortment Master file",
                    "Calculation / Logic": "Direct lookup from Product Details file by matching ASIN.",
                },
                {
                    "Header Name": "PRODUCT SIZE",
                    "Description": "Size category of the product (e.g., Small, Medium, Large). Determines storage allocation and shipping tier.",
                    "Source": "Product Details / Assortment Master file",
                    "Calculation / Logic": "Direct lookup from Product Details file by matching ASIN.",
                },
                {
                    "Header Name": "Portfolio",
                    "Description": "Product portfolio group the ASIN belongs to. Used for strategic grouping and business analysis.",
                    "Source": "Product Details / Assortment Master file",
                    "Calculation / Logic": "Direct lookup from Product Details file by matching ASIN.",
                },
                {
                    "Header Name": "Category",
                    "Description": "Product category classification (e.g., Home & Kitchen, Health & Personal Care). Used for category-level analysis and reporting.",
                    "Source": "Product Details / Assortment Master file",
                    "Calculation / Logic": "Direct lookup from Product Details file by matching ASIN.",
                },
                {
                    "Header Name": "Brand",
                    "Description": "Brand name of the product. Enables brand-level filtering, analysis, and replenishment prioritization.",
                    "Source": "Product Details / Assortment Master file",
                    "Calculation / Logic": "Direct lookup from Product Details file by matching ASIN. If the 'Brand' column exists in the Product Details file, it is merged; otherwise left blank.",
                },
                {
                    "Header Name": "Zone",
                    "Description": "Fulfillment zone name corresponding to the Ideal Cluster. Represents the geographical delivery zone (e.g., MUMBAI, DELHI, BANGALORE).",
                    "Source": "Derived (Internal Mapping)",
                    "Calculation / Logic": "Mapped from Ideal Cluster using a predefined dictionary: BLR_CLUSTER → BANGALORE, BOM_CLUSTER → MUMBAI, HRA_CLUSTER → DELHI, HYD_CLUSTER → HYDERABAD, CHN_CLUSTER → CHENNAI, KOL_CLUSTER → KOLKATA, PUN_CLUSTER → PUNE, AMD_CLUSTER → AHMEDABAD, JAI_CLUSTER → JAIPUR, LKO_CLUSTER → LUCKNOW, IND_CLUSTER → INDORE, NAG_CLUSTER → NAGPUR, PAT_CLUSTER → PATNA, GAA_CLUSTER → GUWAHATI, SAT_CLUSTER → LUDHIANA, BHU_CLUSTER → BHUBANESWAR, COI_CLUSTER → COIMBATORE, HUB_CLUSTER → HUBLI.",
                },
                {
                    "Header Name": "LIS_CLUSTER",
                    "Description": "The mapped Local In-Stock (LIS) cluster name. Used to join LIS Report data with the correct cluster in the master report.",
                    "Source": "Derived (Internal Mapping — same as Zone mapping)",
                    "Calculation / Logic": "Mapped from Ideal Cluster using the same cluster-to-city dictionary as Zone. Used internally to merge LIS_LOCAL_QTY and LIS_TOTAL_QTY from the LIS Report.",
                },
                {
                    "Header Name": "Flex Qty",
                    "Description": "Flexible replenishment quantity provided by the user for a specific ASIN and Cluster combination. Used for manual override or additional quantity planning.",
                    "Source": "Flex Qty file (optional upload with columns: ASIN | Cluster | Qty)",
                    "Calculation / Logic": "Left join on ASIN + Cluster (renamed to Ideal Cluster). If the Flex Qty file is not uploaded, this column is set to 0. Duplicate ASIN + Cluster rows in the Flex Qty file are deduplicated (first value kept).",
                },
                {
                    "Header Name": "National Doc",
                    "Description": "National Days of Cover — the number of days the total national stock for this ASIN will last based on national-level demand. Gives a company-wide view rather than cluster-specific.",
                    "Source": "Calculated (from Stock Qty and DRR columns)",
                    "Calculation / Logic": "Step 1: Sum Stock Qty across ALL clusters for each ASIN → National_Stock_Qty. Step 2: Sum DRR across ALL clusters for each ASIN → National_DRR. Step 3: National Doc = National_Stock_Qty / National_DRR. If DRR is 0 (no sales), National Doc = 0. The same National Doc value appears in every cluster row for a given ASIN.",
                },
                {
                    "Header Name": "DRR",
                    "Description": "Daily Run Rate — the average number of units sold per day for this ASIN in this specific cluster during the sales reporting period.",
                    "Source": "Sales Report (processed)",
                    "Calculation / Logic": "Calculated in the Sales Processor: Total shipped quantity for the ASIN in the cluster ÷ Number of days in the sales report period (from Input Sheet 'Sale Report Days'). Merged by ASIN + Cluster.",
                },
                {
                    "Header Name": "Sales Qty",
                    "Description": "Total sales quantity — the total number of units shipped/sold for this ASIN in this cluster during the sales reporting period.",
                    "Source": "Sales Report (processed)",
                    "Calculation / Logic": "Sum of shipped quantities from the Sales Report grouped by ASIN and Cluster Name. Merged by ASIN + Cluster.",
                },
                {
                    "Header Name": "Stock Qty",
                    "Description": "Current available stock quantity in the warehouse(s) belonging to this cluster for the specific ASIN.",
                    "Source": "Stock Report (processed via FBA Stock Processor)",
                    "Calculation / Logic": "Sum of 'Ending Warehouse Balance' from the Stock Report, grouped by ASIN and Cluster Name (mapped from FC to Cluster via FC Cluster Mapping). Multiple FCs in the same cluster are summed together.",
                },
                {
                    "Header Name": "Receiving Qty",
                    "Description": "Total quantity currently in 'Receiving' status at warehouses — units that have arrived but are not yet available for sale.",
                    "Source": "Shipment Report (processed)",
                    "Calculation / Logic": "Formula: DFC Receiving Qty + IXD Receiving Qty. Both are extracted from the Shipment Report where STATUS = 'Receiving'.",
                },
                {
                    "Header Name": "Stock Transfer Qty",
                    "Description": "Quantity currently in transit between Amazon warehouses (inter-warehouse transfers). Not yet available at the destination.",
                    "Source": "Stock Report",
                    "Calculation / Logic": "Direct from 'In Transit Between Warehouses' column in the Stock Report, summed by ASIN + Cluster.",
                },
                {
                    "Header Name": "Shipment Qty",
                    "Description": "Total shipment pipeline quantity — all units in DFC shipments that are pending, upcoming, or in transit. Represents the total incoming supply via DFC channel.",
                    "Source": "Calculated (from DFC shipment columns)",
                    "Calculation / Logic": "Formula: DFC Appointment Pending Qty + DFC Upcoming Shipment Qty + DFC Intransit Qty.",
                },
                {
                    "Header Name": "P0",
                    "Description": "Priority 0 (Urgent) replenishment demand — the quantity needed to cover the most immediate demand period. Negative values are clipped to 0.",
                    "Source": "Calculated",
                    "Calculation / Logic": "Formula: MAX(0, DRR × P0_Days − Stock Qty − Receiving Qty − Stock Transfer Qty − Shipment Qty). P0_Days default = 15 (configurable via Input Sheet 'P0 Demand DOC').",
                },
                {
                    "Header Name": "P1",
                    "Description": "Priority 1 (High) replenishment demand — additional quantity needed beyond P0 to cover the medium-term demand period. Negative values are clipped to 0.",
                    "Source": "Calculated",
                    "Calculation / Logic": "Formula: MAX(0, DRR × P1_Days − Stock Qty − Receiving Qty − Stock Transfer Qty − Shipment Qty − P0). P1_Days default = 30 (configurable via Input Sheet 'P1 Demand DOC').",
                },
                {
                    "Header Name": "P2",
                    "Description": "Priority 2 (Standard) replenishment demand — quantity needed beyond P0 and P1 to cover the long-term demand period. Negative values are clipped to 0.",
                    "Source": "Calculated",
                    "Calculation / Logic": "Formula: MAX(0, DRR × P2_Days − Stock Qty − Receiving Qty − Stock Transfer Qty − Shipment Qty − P0 − P1). P2_Days default = 60 (configurable via Input Sheet 'P2 Demand DOC').",
                },
                {
                    "Header Name": "Stock Out Date",
                    "Description": "The estimated date when stock will run out for this ASIN in this cluster, considering current stock, receiving pipeline, and incoming shipments.",
                    "Source": "Calculated",
                    "Calculation / Logic": "Step 1: Estimate date when stock + receiving runs out = Today + (Stock Qty + Receiving Qty) / DRR. Step 2: If a DFC shipment is arriving (DFC Next Arrival Date exists) AND the stock-out date from Step 1 falls AFTER the shipment arrival, include shipment qty: Today + (Stock Qty + Receiving Qty + Shipment Qty) / DRR. Step 3: If DRR = 0, Stock Out Date = 'Never'. Displayed as YYYY-MM-DD.",
                },
                {
                    "Header Name": "Stock cover alert",
                    "Description": "A color-coded alert category indicating the health of stock coverage. Helps quickly identify ASINs that need urgent replenishment or are overstocked.",
                    "Source": "Calculated (from Days of Cover = Stock Qty / DRR)",
                    "Calculation / Logic": "Based on Days of Cover (Stock Qty / DRR): <30 Days → '<30 Days' (Critical), <45 Days → '<45 Days' (Warning), 45-60 Days → 'Healthy' (OK), >60 Days → '>60 Days', >90 Days → '>90 Days' (Overstock), >120 Days → '>120 Days' (Excess).",
                },
                {
                    "Header Name": "Page Views - Total",
                    "Description": "Total number of page views (traffic) the product listing received during the business report period. Higher views indicate better visibility.",
                    "Source": "Business Report file",
                    "Calculation / Logic": "Direct lookup from Business Report by matching ASIN (report column '(Child) ASIN'). Deduplicated per ASIN.",
                },
                {
                    "Header Name": "Units Ordered",
                    "Description": "Total number of units ordered by customers for this ASIN during the business report period.",
                    "Source": "Business Report file",
                    "Calculation / Logic": "Direct lookup from Business Report by matching ASIN (report column '(Child) ASIN'). Deduplicated per ASIN.",
                },
                {
                    "Header Name": "Total Order Items",
                    "Description": "Total number of order line items containing this ASIN during the business report period. One order can contain multiple items.",
                    "Source": "Business Report file",
                    "Calculation / Logic": "Direct lookup from Business Report by matching ASIN (report column '(Child) ASIN'). Deduplicated per ASIN.",
                },
                {
                    "Header Name": "Conversion Pct",
                    "Description": "Conversion percentage — the rate at which page views are converting into actual orders. Higher conversion = better listing performance.",
                    "Source": "Calculated (from Business Report data)",
                    "Calculation / Logic": "Formula: (Total Order Items / Page Views - Total) × 100, displayed with '%' suffix. If Page Views = 0, Conversion Pct = 0%. Infinity values are replaced with 0%.",
                },
                {
                    "Header Name": "Ordered Product Sales",
                    "Description": "Total revenue (in ₹) generated from orders of this ASIN during the business report period.",
                    "Source": "Business Report file",
                    "Calculation / Logic": "Direct lookup from Business Report by matching ASIN (report column '(Child) ASIN'). Deduplicated per ASIN.",
                },
                {
                    "Header Name": "LIS_LOCAL_QTY",
                    "Description": "Local In-Stock shipped units — the number of units shipped locally (from the same cluster) for this ASIN. Higher local shipment = better cluster-level stock positioning.",
                    "Source": "LIS Report (Database file)",
                    "Calculation / Logic": "Sum of 'Sum of Local Shipped Units' from the LIS Report, grouped by ASIN + Cluster. Merged using the LIS_CLUSTER mapping (Ideal Cluster → City name).",
                },
                {
                    "Header Name": "LIS_TOTAL_QTY",
                    "Description": "Total In-Stock shipped units — the total number of units shipped (local + non-local) for this ASIN in the LIS cluster. Used with LIS_LOCAL_QTY to calculate local fulfillment percentage.",
                    "Source": "LIS Report (Database file)",
                    "Calculation / Logic": "Sum of 'Sum of Total Units' from the LIS Report, grouped by ASIN + Cluster. Merged using the LIS_CLUSTER mapping. Local Fulfillment % = LIS_LOCAL_QTY / LIS_TOTAL_QTY × 100.",
                },
                {
                    "Header Name": "DFC Appointment Pending Qty",
                    "Description": "Quantity in DFC (Direct Fulfillment Center) shipments that have 'Appointment Pending' status — shipments created but not yet scheduled for delivery to the warehouse.",
                    "Source": "Shipment Report (processed)",
                    "Calculation / Logic": "From Shipment Report where FC Replenishment Type = 'DFC' AND STATUS = 'Appointment Pending'. Sum of FINAL QTY grouped by ASIN + Cluster.",
                },
                {
                    "Header Name": "DFC Upcoming Shipment Qty",
                    "Description": "Quantity in DFC shipments with 'Upcoming' status — shipments that are scheduled and will be dispatched soon.",
                    "Source": "Shipment Report (processed)",
                    "Calculation / Logic": "From Shipment Report where FC Replenishment Type = 'DFC' AND STATUS in upcoming statuses. Sum of FINAL QTY grouped by ASIN + Cluster.",
                },
                {
                    "Header Name": "DFC Intransit Qty",
                    "Description": "Quantity in DFC shipments currently 'In Transit' — shipments that have been dispatched and are on their way to the fulfillment center.",
                    "Source": "Shipment Report (processed)",
                    "Calculation / Logic": "From Shipment Report where FC Replenishment Type = 'DFC' AND STATUS = 'In Transit'. Sum of FINAL QTY grouped by ASIN + Cluster.",
                },
                {
                    "Header Name": "DFC Shipment ID",
                    "Description": "Shipment ID(s) for DFC replenishment — reference number(s) of the active DFC shipments for tracking purposes.",
                    "Source": "Shipment Report (processed)",
                    "Calculation / Logic": "Concatenated Shipment IDs from the Shipment Report for DFC-type shipments, grouped by ASIN + Cluster.",
                },
                {
                    "Header Name": "DFC Next Arrival Date",
                    "Description": "Earliest expected arrival date of the next DFC shipment. Used in Stock Out Date calculation to determine if incoming stock will arrive before current stock runs out.",
                    "Source": "Shipment Report (processed)",
                    "Calculation / Logic": "Minimum APPOINTMENT DATE from DFC shipments for this ASIN + Cluster combination.",
                },
                {
                    "Header Name": "IXD Appointment Pending Qty",
                    "Description": "Quantity in IXD (Inbound Cross-Dock) shipments with 'Appointment Pending' status. IXD shipments are routed through cross-dock centers (ISK3, BLR4, DED5, DED3, HBX1, HBX2).",
                    "Source": "Shipment Report (processed)",
                    "Calculation / Logic": "From Shipment Report where FC Replenishment Type = 'IXD' AND STATUS = 'Appointment Pending'. Sum of FINAL QTY grouped by ASIN + Cluster.",
                },
                {
                    "Header Name": "IXD Upcoming Shipment Qty",
                    "Description": "Quantity in IXD shipments with 'Upcoming' status — scheduled IXD shipments pending dispatch.",
                    "Source": "Shipment Report (processed)",
                    "Calculation / Logic": "From Shipment Report where FC Replenishment Type = 'IXD' AND STATUS in upcoming statuses. Sum of FINAL QTY grouped by ASIN + Cluster.",
                },
                {
                    "Header Name": "IXD Receiving + Intransit Qty",
                    "Description": "Combined quantity in IXD shipments that are either being received at the warehouse or are still in transit.",
                    "Source": "Shipment Report (processed)",
                    "Calculation / Logic": "From Shipment Report where FC Replenishment Type = 'IXD' AND STATUS in ('Receiving', 'In Transit'). Sum of FINAL QTY grouped by ASIN + Cluster.",
                },
                {
                    "Header Name": "IXD Shipment ID",
                    "Description": "Shipment ID(s) for IXD replenishment — reference number(s) of the active IXD shipments for tracking purposes.",
                    "Source": "Shipment Report (processed)",
                    "Calculation / Logic": "Concatenated Shipment IDs from the Shipment Report for IXD-type shipments, grouped by ASIN + Cluster.",
                },
            ]
            df_annexure = pd.DataFrame(annexure_data)
            with pd.ExcelWriter(master_out_excel, engine="xlsxwriter") as writer:
                df_master.to_excel(writer, sheet_name="Master Report", index=False)
                df_annexure.to_excel(writer, sheet_name="Annexure", index=False)
        except Exception:
            master_out_excel = None

        return {
            "status": "success",
            "csv_path": master_out_csv,
            "excel_path": master_out_excel,
            "temp_dir": temp_dir,
            "task_type": "generation",
        }
    except Exception as e:
        return {"status": "error", "message": str(e), "task_type": "generation"}
