from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("dashboard", "0027_dashboardinventoryhealthsummary"),
    ]

    operations = [
        migrations.AddIndex(
            model_name="spenddata",
            index=models.Index(
                fields=["user", "date", "asin", "ad_type"],
                name="idx_spend_u_d_a_t",
            ),
        ),
    ]
