# Generated migration to remove unused Flipkart models
# These models have been replaced by the slim Flipkart pipeline

from django.db import migrations


class Migration(migrations.Migration):
    dependencies = [
        ("dashboard", "0007_flipkartcategorymap_flipkartcoupon_flipkartpca_and_more"),
    ]

    operations = [
        # Delete the old/unused Flipkart models
        # These have been replaced by the slim pipeline models
        migrations.DeleteModel(
            name="FlipkartSalesReport",  # Replaced by FlipkartSalesInvoice
        ),
        migrations.DeleteModel(
            name="FlipkartCurrentInventoryReport",  # Not used in the dashboard pipeline
        ),
        migrations.DeleteModel(
            name="PCADailyReport",  # Replaced by FlipkartPCA
        ),
        migrations.DeleteModel(
            name="PLAFSNReport",  # Replaced by FlipkartPLA
        ),
    ]
