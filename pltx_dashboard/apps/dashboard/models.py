from django.db import models


class SalesData(models.Model):
    user = models.ForeignKey(
        "accounts.Users", on_delete=models.CASCADE, related_name="sales_records"
    )
    date = models.DateField(db_index=True)
    asin = models.CharField(max_length=50, db_index=True)
    pageviews = models.IntegerField(default=0)
    units = models.IntegerField(default=0)
    orders = models.IntegerField(default=0)
    revenue = models.FloatField(default=0.0)

    class Meta:
        unique_together = ("user", "date", "asin")


class SpendData(models.Model):
    user = models.ForeignKey(
        "accounts.Users", on_delete=models.CASCADE, related_name="spend_records"
    )
    date = models.DateField(db_index=True)
    asin = models.CharField(max_length=50, db_index=True)
    ad_account = models.CharField(max_length=100)
    ad_type = models.CharField(max_length=10)
    spend = models.FloatField(default=0.0)

    class Meta:
        unique_together = ("user", "date", "asin", "ad_account", "ad_type")
        indexes = [
            models.Index(
                fields=["user", "date", "asin", "ad_type"],
                name="idx_spend_u_d_a_t",
            ),
            models.Index(
                fields=["user", "date"],
                name="idx_spend_u_date",
            ),
        ]


class CategoryMapping(models.Model):
    user = models.ForeignKey(
        "accounts.Users", on_delete=models.CASCADE, related_name="category_mappings"
    )
    asin = models.CharField(max_length=50, db_index=True)
    portfolio = models.CharField(max_length=100)
    category = models.CharField(max_length=100)
    subcategory = models.CharField(max_length=100)

    class Meta:
        unique_together = ("user", "asin")


class PriceData(models.Model):
    user = models.ForeignKey(
        "accounts.Users", on_delete=models.CASCADE, related_name="price_data"
    )
    asin = models.CharField(max_length=50, db_index=True)
    price = models.FloatField(default=0.0)

    class Meta:
        unique_together = ("user", "asin")


class FBAStockData(models.Model):
    """FBA Stock file — per-ASIN ending warehouse balance."""

    user = models.ForeignKey(
        "accounts.Users", on_delete=models.CASCADE, related_name="fba_stock_data"
    )
    date = models.DateField(db_index=True, null=True, blank=True)
    fnsku = models.CharField(max_length=50, null=True, blank=True)
    asin = models.CharField(max_length=50, db_index=True)
    msku = models.CharField(max_length=100, null=True, blank=True)
    title = models.CharField(max_length=500, null=True, blank=True)
    disposition = models.CharField(max_length=50, null=True, blank=True)
    starting_warehouse_balance = models.IntegerField(default=0)
    in_transit_between_warehouses = models.IntegerField(default=0)
    receipts = models.IntegerField(default=0)
    customer_shipments = models.IntegerField(default=0)
    customer_returns = models.IntegerField(default=0)
    vendor_returns = models.IntegerField(default=0)
    warehouse_transfer_in_out = models.IntegerField(default=0)
    found = models.IntegerField(default=0)
    lost = models.IntegerField(default=0)
    damaged = models.IntegerField(default=0)
    disposed = models.IntegerField(default=0)
    other_events = models.IntegerField(default=0)
    ending_warehouse_balance = models.IntegerField(default=0)
    unknown_events = models.IntegerField(default=0)
    location = models.CharField(max_length=200, null=True, blank=True)

    class Meta:
        unique_together = ("user", "asin", "date", "disposition", "location")
        indexes = [
            models.Index(fields=["user", "asin"], name="idx_fba_user_asin"),
            models.Index(fields=["user", "date", "asin"], name="idx_fba_u_date_asin"),
        ]

    def __str__(self):
        return f"FBA Stock: {self.asin} ({self.ending_warehouse_balance})"


class FlexStockData(models.Model):
    """Flex Stock file — per-ASIN cluster-level stock quantity."""

    user = models.ForeignKey(
        "accounts.Users", on_delete=models.CASCADE, related_name="flex_stock_data"
    )
    date = models.DateField(db_index=True, null=True, blank=True)
    asin = models.CharField(max_length=50, db_index=True)
    cluster = models.CharField(max_length=100, null=True, blank=True)
    qty = models.IntegerField(default=0)

    class Meta:
        unique_together = ("user", "asin", "date", "cluster")
        indexes = [
            models.Index(fields=["user", "asin"], name="idx_flex_user_asin"),
            models.Index(fields=["user", "asin", "date"], name="idx_flex_u_a_d"),
            models.Index(fields=["user", "date", "asin"], name="idx_flex_u_date_asin"),
        ]

    def __str__(self):
        return f"Flex Stock: {self.asin} ({self.qty}) [{self.date}]"


class ProcessedDashboardData(models.Model):
    user = models.ForeignKey(
        "accounts.Users",
        on_delete=models.CASCADE,
        related_name="processed_dashboard_data",
    )
    date = models.DateField(db_index=True)
    asin = models.CharField(max_length=50, db_index=True)
    portfolio = models.CharField(max_length=100, db_index=True, null=True, blank=True)
    category = models.CharField(max_length=100, db_index=True, null=True, blank=True)
    subcategory = models.CharField(max_length=100, db_index=True, null=True, blank=True)
    price = models.FloatField(default=0.0)

    pageviews = models.IntegerField(default=0)
    units = models.IntegerField(default=0)
    orders = models.IntegerField(default=0)
    revenue = models.FloatField(default=0.0)

    spend_sp = models.FloatField(default=0.0)
    spend_sb = models.FloatField(default=0.0)
    spend_sd = models.FloatField(default=0.0)
    total_spend = models.FloatField(default=0.0)

    class Meta:
        unique_together = ("user", "date", "asin")
        indexes = [
            models.Index(fields=["user", "category", "date"], name="idx_user_cat_date"),
            models.Index(fields=["user", "date"], name="idx_user_date"),
            models.Index(fields=["user", "asin", "date"], name="idx_user_asin_date"),
            models.Index(fields=["user", "asin"], name="idx_pdd_u_asin"),
            models.Index(fields=["user", "category"], name="idx_pdd_u_cat"),
            models.Index(fields=["user", "portfolio"], name="idx_pdd_u_port"),
            models.Index(fields=["user", "subcategory"], name="idx_pdd_u_sub"),
            models.Index(
                fields=["user", "date", "portfolio", "category"],
                name="idx_u_d_p_c",
            ),
            models.Index(fields=["user", "portfolio", "date"], name="idx_pdd_u_port_date"),
            models.Index(fields=["user", "subcategory", "date"], name="idx_pdd_u_sub_date"),
            models.Index(fields=["user", "category", "asin"], name="idx_pdd_u_cat_asn"),
            models.Index(fields=["user", "portfolio", "asin"], name="idx_pdd_u_prt_asn"),
            models.Index(fields=["user", "subcategory", "asin"], name="idx_pdd_u_sub_asn"),
            models.Index(
                fields=["user", "category", "portfolio", "subcategory", "date"],
                name="idx_pdd_u_c_p_s_d",
            ),
            models.Index(
                fields=["user", "date", "category", "portfolio", "subcategory"],
                name="idx_pdd_u_d_c_p_s",
            ),
        ]


# ============================================================================
# SLIM FLIPKART MODELS (dashboard pipeline — only required columns)
# ============================================================================


class FlipkartSearchTraffic(models.Model):
    """Search Traffic Report — FSN-level traffic & sales per date."""

    user = models.ForeignKey(
        "accounts.Users", on_delete=models.CASCADE, related_name="fk_search_traffic"
    )
    fsn = models.CharField(max_length=50, db_index=True)
    sku = models.CharField(max_length=100, null=True, blank=True)
    vertical = models.CharField(max_length=100, null=True, blank=True)
    date = models.DateField(db_index=True)
    page_views = models.IntegerField(default=0)
    product_clicks = models.IntegerField(default=0)
    sales = models.IntegerField(default=0)
    revenue = models.FloatField(default=0.0)

    class Meta:
        unique_together = ("user", "fsn", "date")
        indexes = [
            models.Index(fields=["user", "date", "fsn"], name="idx_fkst_u_d_fsn"),
        ]


class FlipkartCategoryMap(models.Model):
    """Category Dashboard — FSN → Portfolio / Category / SubCategory."""

    user = models.ForeignKey(
        "accounts.Users", on_delete=models.CASCADE, related_name="fk_category_maps"
    )
    fsn = models.CharField(max_length=50, db_index=True)
    sku = models.CharField(max_length=100, null=True, blank=True)
    portfolio = models.CharField(max_length=100, null=True, blank=True)
    category = models.CharField(max_length=100, null=True, blank=True)
    subcategory = models.CharField(max_length=100, null=True, blank=True)
    product_status = models.CharField(max_length=30, null=True, blank=True, db_index=True)

    class Meta:
        unique_together = ("user", "fsn")
        indexes = [
            models.Index(fields=["user", "category"], name="idx_fkcat_u_cat"),
            models.Index(fields=["user", "portfolio"], name="idx_fkcat_u_port"),
            models.Index(fields=["user", "subcategory"], name="idx_fkcat_u_sub"),
            models.Index(fields=["user", "product_status"], name="idx_fkcat_u_status"),
        ]


class FlipkartPrice(models.Model):
    """FK Price — FSN → Deal price."""

    user = models.ForeignKey(
        "accounts.Users", on_delete=models.CASCADE, related_name="fk_price_data"
    )
    fsn = models.CharField(max_length=50, db_index=True)
    price = models.FloatField(default=0.0)

    class Meta:
        unique_together = ("user", "fsn")


class FlipkartPLA(models.Model):
    """PLA FSN Report — campaign_id + FSN → Ad Spend."""

    user = models.ForeignKey(
        "accounts.Users", on_delete=models.CASCADE, related_name="fk_pla_reports"
    )
    campaign_id = models.CharField(max_length=100, db_index=True)
    fsn_id = models.CharField(max_length=100, db_index=True)
    date = models.DateField(db_index=True, null=True, blank=True)
    ad_spend = models.FloatField(default=0.0)

    class Meta:
        unique_together = ("user", "campaign_id", "fsn_id", "date")
        indexes = [
            models.Index(fields=["user", "fsn_id", "date"], name="idx_fkpla_u_fsn_date"),
            models.Index(fields=["user", "date"], name="idx_fkpla_u_date"),
        ]


class FlipkartInventoryStock(models.Model):
    """FK Inventory file — FSN-level stock snapshot per date (FK.xlsx)."""

    user = models.ForeignKey(
        "accounts.Users", on_delete=models.CASCADE, related_name="fk_inventory_stock"
    )
    date = models.DateField(db_index=True)
    fsn = models.CharField(max_length=50, db_index=True)
    sku = models.CharField(max_length=200, null=True, blank=True)
    product_status = models.CharField(max_length=50, null=True, blank=True)
    product_type = models.CharField(max_length=200, null=True, blank=True)
    qty = models.IntegerField(default=0)

    class Meta:
        unique_together = ("user", "fsn", "date")
        indexes = [
            models.Index(fields=["user", "date", "fsn"], name="idx_fkinv_u_d_fsn"),
            models.Index(fields=["user", "fsn"], name="idx_fkinv_u_fsn"),
        ]
        verbose_name = "Flipkart Inventory Stock"

    def __str__(self):
        return f"FK Inventory: {self.fsn} ({self.qty}) [{self.date}]"


class Flipkartfba(models.Model):
    """Flipkart FBA current inventory report (FSN/date-level)."""

    user = models.ForeignKey(
        "accounts.Users", on_delete=models.CASCADE, related_name="fk_fba_stock_data"
    )
    date = models.DateField(db_index=True)
    fsn = models.CharField(max_length=50, db_index=True)
    warehouse_id = models.CharField(max_length=200, null=True, blank=True)
    sku = models.CharField(max_length=200, null=True, blank=True)
    title = models.CharField(max_length=500, null=True, blank=True)
    listing_id = models.CharField(max_length=120, null=True, blank=True)
    brand = models.CharField(max_length=120, null=True, blank=True)
    flipkart_selling_price = models.FloatField(default=0.0)
    live_on_website = models.IntegerField(default=0)

    class Meta:
        unique_together = ("user", "date", "fsn", "warehouse_id")
        indexes = [
            models.Index(fields=["user", "date", "fsn"], name="idx_fkfba_u_d_fsn"),
            models.Index(fields=["user", "fsn"], name="idx_fkfba_u_fsn"),
        ]
        verbose_name = "Flipkart FBA"

    def __str__(self):
        return f"FK FBA: {self.fsn} ({self.live_on_website}) [{self.date}]"


class FlipkartProcessedDashboardData(models.Model):
    """Final merged Flipkart data — analogous to ProcessedDashboardData."""

    user = models.ForeignKey(
        "accounts.Users",
        on_delete=models.CASCADE,
        related_name="fk_processed_dashboard",
    )
    date = models.DateField(db_index=True)
    fsn = models.CharField(max_length=50, db_index=True)
    platform = models.CharField(max_length=20, default="Flipkart")
    portfolio = models.CharField(max_length=100, db_index=True, null=True, blank=True)
    category = models.CharField(max_length=100, db_index=True, null=True, blank=True)
    subcategory = models.CharField(max_length=100, db_index=True, null=True, blank=True)
    price = models.FloatField(default=0.0)

    pageviews = models.IntegerField(default=0)
    units = models.IntegerField(default=0)
    orders = models.IntegerField(default=0)  # always 0 for Flipkart (no order data)
    revenue = models.FloatField(default=0.0)

    total_spend = models.FloatField(default=0.0)
    spend_sp = models.FloatField(
        default=0.0
    )  # not split for Flipkart; all in total_spend
    spend_sb = models.FloatField(default=0.0)
    spend_sd = models.FloatField(default=0.0)

    class Meta:
        unique_together = ("user", "date", "fsn")
        indexes = [
            models.Index(
                fields=["user", "category", "date"], name="idx_fk_user_cat_date"
            ),
            models.Index(fields=["user", "date"], name="idx_fk_user_date"),
            models.Index(fields=["user", "fsn", "date"], name="idx_fk_user_fsn_date"),
            models.Index(fields=["user", "fsn"], name="idx_fkpdd_u_fsn"),
            models.Index(fields=["user", "category"], name="idx_fkpdd_u_cat"),
            models.Index(fields=["user", "portfolio"], name="idx_fkpdd_u_port"),
            models.Index(fields=["user", "subcategory"], name="idx_fkpdd_u_sub"),
            models.Index(
                fields=["user", "date", "portfolio", "category"],
                name="idx_fk_u_d_p_c",
            ),
            models.Index(
                fields=["user", "portfolio", "date"],
                name="idx_fkpdd_u_port_date",
            ),
            models.Index(
                fields=["user", "subcategory", "date"],
                name="idx_fkpdd_u_sub_date",
            ),
            models.Index(fields=["user", "category", "fsn"], name="idx_fkpd_u_cat_fsn"),
            models.Index(fields=["user", "portfolio", "fsn"], name="idx_fkpd_u_prt_fsn"),
            models.Index(fields=["user", "subcategory", "fsn"], name="idx_fkpd_u_sub_fsn"),
            models.Index(
                fields=["user", "category", "portfolio", "subcategory", "date"],
                name="idx_fkpd_u_c_p_s_d",
            ),
            models.Index(
                fields=["user", "date", "category", "portfolio", "subcategory"],
                name="idx_fkpd_u_d_c_p_s",
            ),
        ]


class DashboardMaterializedSummary(models.Model):
    """
    Persistent payload cache keyed by normalized filters + data version.
    Keeps repeated dashboard filter views fast even across process restarts.
    """

    user = models.ForeignKey(
        "accounts.Users",
        on_delete=models.CASCADE,
        related_name="dashboard_materialized_summaries",
    )
    view_type = models.CharField(max_length=20, default="shared", db_index=True)
    data_version = models.BigIntegerField(default=0, db_index=True)
    filter_hash = models.CharField(max_length=64, db_index=True)
    normalized_filters = models.TextField()
    payload_blob = models.BinaryField()
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        unique_together = ("user", "view_type", "data_version", "filter_hash")
        indexes = [
            models.Index(
                fields=["user", "view_type", "data_version"],
                name="idx_dms_u_v_dv",
            ),
            models.Index(fields=["user", "updated_at"], name="idx_dms_u_upd"),
        ]


class DashboardDailySummary(models.Model):
    """
    Pre-aggregated day-level metrics for faster dashboard reads.
    Dimensions intentionally exclude ASIN/FSN so table stays compact.
    """

    user = models.ForeignKey(
        "accounts.Users",
        on_delete=models.CASCADE,
        related_name="dashboard_daily_summaries",
    )
    date = models.DateField(db_index=True)
    platform = models.CharField(max_length=20, db_index=True)  # Amazon / Flipkart
    category = models.CharField(max_length=100, null=True, blank=True, db_index=True)
    portfolio = models.CharField(max_length=100, null=True, blank=True, db_index=True)
    subcategory = models.CharField(max_length=100, null=True, blank=True, db_index=True)

    revenue = models.FloatField(default=0.0)
    orders = models.IntegerField(default=0)
    units = models.IntegerField(default=0)
    pageviews = models.IntegerField(default=0)
    total_spend = models.FloatField(default=0.0)
    spend_sp = models.FloatField(default=0.0)
    spend_sb = models.FloatField(default=0.0)
    spend_sd = models.FloatField(default=0.0)

    class Meta:
        unique_together = (
            "user",
            "date",
            "platform",
            "category",
            "portfolio",
            "subcategory",
        )
        indexes = [
            models.Index(fields=["user", "date", "platform"], name="idx_dds_u_d_plat"),
            models.Index(fields=["user", "platform", "date"], name="idx_dds_u_plat_d"),
            models.Index(fields=["user", "category", "date"], name="idx_dds_u_cat_d"),
            models.Index(fields=["user", "portfolio", "date"], name="idx_dds_u_port_d"),
            models.Index(fields=["user", "subcategory", "date"], name="idx_dds_u_sub_d"),
            models.Index(fields=["user", "platform", "category", "date"], name="idx_dds_u_p_cat_d"),
            models.Index(fields=["user", "platform", "portfolio", "date"], name="idx_dds_u_p_port_d"),
            models.Index(fields=["user", "platform", "subcategory", "date"], name="idx_dds_u_p_sub_d"),
        ]


class DashboardAsinMonthlySummary(models.Model):
    """
    Monthly pre-aggregated metrics per ASIN/FSN — one row per (user, platform, asin, year_month).
    Rebuilt by Celery after each upload. Eliminates expensive GROUP BY asin scans
    on ProcessedDashboardData for long date ranges (last 3 months / 6 months / 1 year).
    year_month is stored as the first day of the month, e.g. 2026-05-01.
    """

    user = models.ForeignKey(
        "accounts.Users",
        on_delete=models.CASCADE,
        related_name="asin_monthly_summaries",
    )
    platform = models.CharField(max_length=20, db_index=True)  # Amazon / Flipkart
    asin = models.CharField(max_length=50, db_index=True)  # asin (Amazon) or fsn (Flipkart)
    year_month = models.DateField(db_index=True)  # first day of month: 2026-05-01

    portfolio = models.CharField(max_length=100, null=True, blank=True)
    category = models.CharField(max_length=100, null=True, blank=True)
    subcategory = models.CharField(max_length=100, null=True, blank=True)

    revenue = models.FloatField(default=0.0)
    orders = models.IntegerField(default=0)
    units = models.IntegerField(default=0)
    pageviews = models.IntegerField(default=0)
    total_spend = models.FloatField(default=0.0)
    spend_sp = models.FloatField(default=0.0)
    spend_sb = models.FloatField(default=0.0)
    spend_sd = models.FloatField(default=0.0)

    class Meta:
        unique_together = ("user", "platform", "asin", "year_month")
        indexes = [
            models.Index(fields=["user", "platform", "year_month"], name="idx_ams_u_plat_ym"),
            models.Index(fields=["user", "asin", "year_month"], name="idx_ams_u_a_ym"),
            models.Index(fields=["user", "category", "year_month"], name="idx_ams_u_cat_ym"),
            models.Index(fields=["user", "portfolio", "year_month"], name="idx_ams_u_port_ym"),
            models.Index(fields=["user", "platform", "asin"], name="idx_ams_u_plat_a"),
            models.Index(fields=["user", "subcategory", "year_month"], name="idx_ams_u_sub_ym"),
            models.Index(fields=["user", "platform", "category", "year_month"], name="idx_ams_u_p_cat_ym"),
            models.Index(fields=["user", "platform", "portfolio", "year_month"], name="idx_ams_u_p_port_ym"),
            models.Index(fields=["user", "platform", "subcategory", "year_month"], name="idx_ams_u_p_sub_ym"),
        ]


class DashboardInventoryHealthSummary(models.Model):
    """
    Precomputed inventory-health rows used by dashboard requests.
    One row per user/platform/date/sku.
    """

    user = models.ForeignKey(
        "accounts.Users",
        on_delete=models.CASCADE,
        related_name="dashboard_inventory_health_summaries",
    )
    date = models.DateField(db_index=True)
    platform = models.CharField(max_length=20, db_index=True)  # Amazon / Flipkart
    sku = models.CharField(max_length=80, db_index=True)

    category = models.CharField(max_length=120, null=True, blank=True, db_index=True)
    portfolio = models.CharField(max_length=120, null=True, blank=True, db_index=True)
    subcategory = models.CharField(max_length=120, null=True, blank=True, db_index=True)

    stock_qty = models.IntegerField(default=0)
    fba_qty = models.IntegerField(default=0)
    flex_qty = models.IntegerField(default=0)
    sale_qty = models.IntegerField(default=0)
    total_sales_window = models.IntegerField(default=0)
    drr = models.FloatField(default=0.0)
    doc = models.FloatField(default=0.0)
    revenue = models.FloatField(default=0.0)

    status = models.CharField(max_length=50, db_index=True)
    status_class = models.CharField(max_length=30, db_index=True)
    reason = models.TextField(blank=True, default="")

    class Meta:
        unique_together = ("user", "date", "platform", "sku")
        indexes = [
            models.Index(fields=["user", "platform", "date"], name="idx_dihs_u_p_d"),
            models.Index(fields=["user", "platform", "sku", "date"], name="idx_dihs_u_p_s_d"),
            models.Index(fields=["user", "platform", "category"], name="idx_dihs_u_p_cat"),
            models.Index(fields=["user", "platform", "portfolio"], name="idx_dihs_u_p_port"),
            models.Index(fields=["user", "platform", "subcategory"], name="idx_dihs_u_p_sub"),
            models.Index(fields=["user", "platform", "status"], name="idx_dihs_u_p_status"),
            models.Index(fields=["user", "platform", "category", "date"], name="idx_dihs_u_p_cat_d"),
            models.Index(fields=["user", "platform", "portfolio", "date"], name="idx_dihs_u_p_port_d"),
            models.Index(fields=["user", "platform", "subcategory", "date"], name="idx_dihs_u_p_sub_d"),
        ]
