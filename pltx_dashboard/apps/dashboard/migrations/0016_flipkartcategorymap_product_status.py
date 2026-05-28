from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("dashboard", "0015_alter_flexstockdata_unique_together_and_more"),
    ]

    operations = [
        migrations.AddField(
            model_name="flipkartcategorymap",
            name="product_status",
            field=models.CharField(
                blank=True, db_index=True, max_length=30, null=True
            ),
        ),
    ]

