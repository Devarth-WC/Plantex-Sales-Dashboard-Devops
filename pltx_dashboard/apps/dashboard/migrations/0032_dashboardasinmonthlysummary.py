from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ("dashboard", "0031_add_spend_date_index"),
    ]

    operations = [
        migrations.CreateModel(
            name="DashboardAsinMonthlySummary",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("platform", models.CharField(db_index=True, max_length=20)),
                ("asin", models.CharField(db_index=True, max_length=50)),
                ("year_month", models.DateField(db_index=True)),
                ("portfolio", models.CharField(blank=True, max_length=100, null=True)),
                ("category", models.CharField(blank=True, max_length=100, null=True)),
                ("subcategory", models.CharField(blank=True, max_length=100, null=True)),
                ("revenue", models.FloatField(default=0.0)),
                ("orders", models.IntegerField(default=0)),
                ("units", models.IntegerField(default=0)),
                ("pageviews", models.IntegerField(default=0)),
                ("total_spend", models.FloatField(default=0.0)),
                ("spend_sp", models.FloatField(default=0.0)),
                ("spend_sb", models.FloatField(default=0.0)),
                ("spend_sd", models.FloatField(default=0.0)),
                ("user", models.ForeignKey(
                    on_delete=django.db.models.deletion.CASCADE,
                    related_name="asin_monthly_summaries",
                    to="accounts.users",
                )),
            ],
            options={
                "unique_together": {("user", "platform", "asin", "year_month")},
            },
        ),
        migrations.AddIndex(
            model_name="dashboardasinmonthlysummary",
            index=models.Index(fields=["user", "platform", "year_month"], name="idx_ams_u_plat_ym"),
        ),
        migrations.AddIndex(
            model_name="dashboardasinmonthlysummary",
            index=models.Index(fields=["user", "asin", "year_month"], name="idx_ams_u_a_ym"),
        ),
        migrations.AddIndex(
            model_name="dashboardasinmonthlysummary",
            index=models.Index(fields=["user", "category", "year_month"], name="idx_ams_u_cat_ym"),
        ),
        migrations.AddIndex(
            model_name="dashboardasinmonthlysummary",
            index=models.Index(fields=["user", "portfolio", "year_month"], name="idx_ams_u_port_ym"),
        ),
        migrations.AddIndex(
            model_name="dashboardasinmonthlysummary",
            index=models.Index(fields=["user", "platform", "asin"], name="idx_ams_u_plat_a"),
        ),
    ]
