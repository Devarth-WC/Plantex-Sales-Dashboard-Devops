from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("dashboard", "0022_flipkartprocesseddashboarddata_idx_fkpdd_u_fsn_and_more"),
    ]

    operations = [
        migrations.AddIndex(
            model_name="processeddashboarddata",
            index=models.Index(
                fields=["user", "category", "asin"], name="idx_pdd_u_cat_asn"
            ),
        ),
        migrations.AddIndex(
            model_name="processeddashboarddata",
            index=models.Index(
                fields=["user", "portfolio", "asin"], name="idx_pdd_u_prt_asn"
            ),
        ),
        migrations.AddIndex(
            model_name="processeddashboarddata",
            index=models.Index(
                fields=["user", "subcategory", "asin"], name="idx_pdd_u_sub_asn"
            ),
        ),
        migrations.AddIndex(
            model_name="flipkartprocesseddashboarddata",
            index=models.Index(
                fields=["user", "category", "fsn"], name="idx_fkpd_u_cat_fsn"
            ),
        ),
        migrations.AddIndex(
            model_name="flipkartprocesseddashboarddata",
            index=models.Index(
                fields=["user", "portfolio", "fsn"], name="idx_fkpd_u_prt_fsn"
            ),
        ),
        migrations.AddIndex(
            model_name="flipkartprocesseddashboarddata",
            index=models.Index(
                fields=["user", "subcategory", "fsn"], name="idx_fkpd_u_sub_fsn"
            ),
        ),
    ]
