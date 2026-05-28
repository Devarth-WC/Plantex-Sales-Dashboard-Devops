from django.db.models.signals import post_delete, post_save
from django.dispatch import receiver

from apps.dashboard.models import (
    CategoryMapping,
    FBAStockData,
    FlexStockData,
    FlipkartCategoryMap,
    FlipkartInventoryStock,
    FlipkartPLA,
    FlipkartPrice,
    FlipkartProcessedDashboardData,
    FlipkartSearchTraffic,
    PriceData,
    ProcessedDashboardData,
    SalesData,
    SpendData,
)
from apps.dashboard.services.invalidation import invalidate_dashboard_cache_for_user


INVALIDATION_MODELS = (
    SalesData,
    SpendData,
    CategoryMapping,
    PriceData,
    FBAStockData,
    FlexStockData,
    ProcessedDashboardData,
    FlipkartSearchTraffic,
    FlipkartCategoryMap,
    FlipkartPrice,
    FlipkartPLA,
    FlipkartInventoryStock,
    FlipkartProcessedDashboardData,
)


@receiver(post_delete, dispatch_uid="dashboard_cache_invalidate_delete")
@receiver(post_save, dispatch_uid="dashboard_cache_invalidate_save")
def _invalidate_on_change(sender, instance, **kwargs):
    if sender not in INVALIDATION_MODELS:
        return
    user_id = getattr(instance, "user_id", None)
    if user_id:
        invalidate_dashboard_cache_for_user(user_id, clear_materialized=True)
