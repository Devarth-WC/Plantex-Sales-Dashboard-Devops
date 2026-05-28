from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ("dashboard", "0025_flipkartinventorystock_add_date"),
    ]

    operations = [
        migrations.CreateModel(
            name="DashboardDailySummary",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("date", models.DateField(db_index=True)),
                ("platform", models.CharField(db_index=True, max_length=20)),
                ("category", models.CharField(blank=True, db_index=True, max_length=100, null=True)),
                ("portfolio", models.CharField(blank=True, db_index=True, max_length=100, null=True)),
                ("subcategory", models.CharField(blank=True, db_index=True, max_length=100, null=True)),
                ("revenue", models.FloatField(default=0.0)),
                ("orders", models.IntegerField(default=0)),
                ("units", models.IntegerField(default=0)),
                ("pageviews", models.IntegerField(default=0)),
                ("total_spend", models.FloatField(default=0.0)),
                ("spend_sp", models.FloatField(default=0.0)),
                ("spend_sb", models.FloatField(default=0.0)),
                ("spend_sd", models.FloatField(default=0.0)),
                ("user", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="dashboard_daily_summaries", to="accounts.users")),
            ],
            options={
                "unique_together": {
                    ("user", "date", "platform", "category", "portfolio", "subcategory")
                },
            },
        ),
        migrations.AddIndex(
            model_name="dashboarddailysummary",
            index=models.Index(fields=["user", "date", "platform"], name="idx_dds_u_d_plat"),
        ),
        migrations.AddIndex(
            model_name="dashboarddailysummary",
            index=models.Index(fields=["user", "platform", "date"], name="idx_dds_u_plat_d"),
        ),
        migrations.AddIndex(
            model_name="dashboarddailysummary",
            index=models.Index(fields=["user", "category", "date"], name="idx_dds_u_cat_d"),
        ),
        migrations.AddIndex(
            model_name="dashboarddailysummary",
            index=models.Index(fields=["user", "portfolio", "date"], name="idx_dds_u_port_d"),
        ),
        migrations.AddIndex(
            model_name="dashboarddailysummary",
            index=models.Index(fields=["user", "subcategory", "date"], name="idx_dds_u_sub_d"),
        ),
        migrations.AddIndex(
            model_name="salesdata",
            index=models.Index(fields=["user", "date"], name="idx_sales_u_date"),
        ),
        migrations.AddIndex(
            model_name="salesdata",
            index=models.Index(fields=["user", "asin"], name="idx_sales_u_asin"),
        ),
        migrations.AddIndex(
            model_name="spenddata",
            index=models.Index(fields=["user", "date"], name="idx_spend_u_date"),
        ),
        migrations.AddIndex(
            model_name="spenddata",
            index=models.Index(fields=["user", "asin"], name="idx_spend_u_asin"),
        ),
        migrations.AddIndex(
            model_name="categorymapping",
            index=models.Index(fields=["user", "category"], name="idx_cm_u_cat"),
        ),
        migrations.AddIndex(
            model_name="categorymapping",
            index=models.Index(fields=["user", "portfolio"], name="idx_cm_u_port"),
        ),
        migrations.AddIndex(
            model_name="categorymapping",
            index=models.Index(fields=["user", "subcategory"], name="idx_cm_u_sub"),
        ),
        migrations.AddIndex(
            model_name="flipkartsearchtraffic",
            index=models.Index(fields=["user", "date"], name="idx_fkst_u_date"),
        ),
        migrations.AddIndex(
            model_name="flipkartsearchtraffic",
            index=models.Index(fields=["user", "fsn"], name="idx_fkst_u_fsn"),
        ),
    ]
