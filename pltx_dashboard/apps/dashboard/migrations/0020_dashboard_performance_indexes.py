from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("dashboard", "0019_flipkartinventorystock"),
    ]

    operations = [
        migrations.AddIndex(
            model_name="fbastockdata",
            index=models.Index(
                fields=["user", "date", "asin"], name="idx_fba_u_date_asin"
            ),
        ),
        migrations.AddIndex(
            model_name="flexstockdata",
            index=models.Index(
                fields=["user", "date", "asin"], name="idx_flex_u_date_asin"
            ),
        ),
        migrations.AddIndex(
            model_name="processeddashboarddata",
            index=models.Index(
                fields=["user", "portfolio", "date"], name="idx_pdd_u_port_date"
            ),
        ),
        migrations.AddIndex(
            model_name="processeddashboarddata",
            index=models.Index(
                fields=["user", "subcategory", "date"], name="idx_pdd_u_sub_date"
            ),
        ),
        migrations.AddIndex(
            model_name="flipkartcategorymap",
            index=models.Index(fields=["user", "category"], name="idx_fkcat_u_cat"),
        ),
        migrations.AddIndex(
            model_name="flipkartcategorymap",
            index=models.Index(fields=["user", "portfolio"], name="idx_fkcat_u_port"),
        ),
        migrations.AddIndex(
            model_name="flipkartcategorymap",
            index=models.Index(fields=["user", "subcategory"], name="idx_fkcat_u_sub"),
        ),
        migrations.AddIndex(
            model_name="flipkartcategorymap",
            index=models.Index(
                fields=["user", "product_status"], name="idx_fkcat_u_status"
            ),
        ),
        migrations.AddIndex(
            model_name="flipkartpla",
            index=models.Index(
                fields=["user", "fsn_id", "date"], name="idx_fkpla_u_fsn_date"
            ),
        ),
        migrations.AddIndex(
            model_name="flipkartprocesseddashboarddata",
            index=models.Index(
                fields=["user", "portfolio", "date"], name="idx_fkpdd_u_port_date"
            ),
        ),
        migrations.AddIndex(
            model_name="flipkartprocesseddashboarddata",
            index=models.Index(
                fields=["user", "subcategory", "date"], name="idx_fkpdd_u_sub_date"
            ),
        ),
    ]
