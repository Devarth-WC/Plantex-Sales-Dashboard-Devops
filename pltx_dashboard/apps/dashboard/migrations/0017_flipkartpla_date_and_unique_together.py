from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("dashboard", "0016_flipkartcategorymap_product_status"),
    ]

    operations = [
        migrations.AddField(
            model_name="flipkartpla",
            name="date",
            field=models.DateField(blank=True, db_index=True, null=True),
        ),
        migrations.AlterUniqueTogether(
            name="flipkartpla",
            unique_together={("user", "campaign_id", "fsn_id", "date")},
        ),
    ]
