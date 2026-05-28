from django.contrib import admin
from .models import (
    SalesData,
    SpendData,
    CategoryMapping,
    PriceData,
    FBAStockData,
    FlexStockData,
    ProcessedDashboardData,
    FlipkartSearchTraffic,
    FlipkartCategoryMap,
    Flipkartfba,
    FlipkartPrice,
    FlipkartPLA,
    FlipkartProcessedDashboardData,
)


@admin.register(SalesData)
class SalesDataAdmin(admin.ModelAdmin):
    list_display = ("date", "asin", "revenue", "orders")
    list_filter = ("date", "asin")
    search_fields = ("asin",)


@admin.register(SpendData)
class SpendDataAdmin(admin.ModelAdmin):
    list_display = ("date", "asin", "ad_type", "spend")
    list_filter = ("date", "ad_type", "asin")


@admin.register(CategoryMapping)
class CategoryMappingAdmin(admin.ModelAdmin):
    list_display = ("user", "asin", "portfolio", "category")
    list_filter = ("portfolio", "category")


@admin.register(PriceData)
class PriceDataAdmin(admin.ModelAdmin):
    list_display = ("user", "asin", "price")


@admin.register(FBAStockData)
class FBAStockDataAdmin(admin.ModelAdmin):
    list_display = ("user", "date", "asin", "fnsku", "ending_warehouse_balance", "location")
    list_filter = ("date", "user")
    search_fields = ("asin", "fnsku", "msku")


@admin.register(FlexStockData)
class FlexStockDataAdmin(admin.ModelAdmin):
    list_display = ("user", "asin", "cluster", "qty")
    list_filter = ("user",)
    search_fields = ("asin", "cluster")


@admin.register(ProcessedDashboardData)
class ProcessedDashboardDataAdmin(admin.ModelAdmin):
    list_display = ("user", "date", "asin", "revenue", "total_spend")
    list_filter = ("date", "user")


# ─── Slim Flipkart Models Admin ───


@admin.register(FlipkartSearchTraffic)
class FlipkartSearchTrafficAdmin(admin.ModelAdmin):
    list_display = ("user", "date", "fsn", "sku", "revenue")
    list_filter = ("date", "user")
    search_fields = ("fsn", "sku")


@admin.register(FlipkartCategoryMap)
class FlipkartCategoryMapAdmin(admin.ModelAdmin):
    list_display = (
        "user",
        "fsn",
        "portfolio",
        "category",
        "subcategory",
        "product_status",
    )
    list_filter = ("portfolio", "category", "product_status")
    search_fields = ("fsn", "sku")


@admin.register(FlipkartPrice)
class FlipkartPriceAdmin(admin.ModelAdmin):
    list_display = ("user", "fsn", "price")
    search_fields = ("fsn",)


@admin.register(Flipkartfba)
class FlipkartfbaAdmin(admin.ModelAdmin):
    list_display = ("user", "date", "fsn", "warehouse_id", "live_on_website")
    list_filter = ("date", "user")
    search_fields = ("fsn", "sku", "listing_id")


@admin.register(FlipkartPLA)
class FlipkartPLAAdmin(admin.ModelAdmin):
    list_display = ("user", "date", "campaign_id", "fsn_id", "ad_spend")
    list_filter = ("date", "user")
    search_fields = ("campaign_id", "fsn_id")


@admin.register(FlipkartProcessedDashboardData)
class FlipkartProcessedDashboardDataAdmin(admin.ModelAdmin):
    list_display = (
        "user",
        "date",
        "fsn",
        "platform",
        "revenue",
        "total_spend",
    )
    list_filter = ("date", "user")
    search_fields = ("fsn", "category")
