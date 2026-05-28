from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ("dashboard", "0026_dashboarddailysummary_and_more_indexes"),
    ]

    operations = [
        migrations.CreateModel(
            name="DashboardInventoryHealthSummary",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("date", models.DateField(db_index=True)),
                ("platform", models.CharField(db_index=True, max_length=20)),
                ("sku", models.CharField(db_index=True, max_length=80)),
                ("category", models.CharField(blank=True, db_index=True, max_length=120, null=True)),
                ("portfolio", models.CharField(blank=True, db_index=True, max_length=120, null=True)),
                ("subcategory", models.CharField(blank=True, db_index=True, max_length=120, null=True)),
                ("stock_qty", models.IntegerField(default=0)),
                ("fba_qty", models.IntegerField(default=0)),
                ("flex_qty", models.IntegerField(default=0)),
                ("sale_qty", models.IntegerField(default=0)),
                ("total_sales_window", models.IntegerField(default=0)),
                ("drr", models.FloatField(default=0.0)),
                ("doc", models.FloatField(default=0.0)),
                ("revenue", models.FloatField(default=0.0)),
                ("status", models.CharField(db_index=True, max_length=50)),
                ("status_class", models.CharField(db_index=True, max_length=30)),
                ("reason", models.TextField(blank=True, default="")),
                ("user", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="dashboard_inventory_health_summaries", to="accounts.users")),
            ],
            options={
                "unique_together": {("user", "date", "platform", "sku")},
            },
        ),
        migrations.AddIndex(
            model_name="dashboardinventoryhealthsummary",
            index=models.Index(fields=["user", "platform", "date"], name="idx_dihs_u_p_d"),
        ),
        migrations.AddIndex(
            model_name="dashboardinventoryhealthsummary",
            index=models.Index(fields=["user", "platform", "sku", "date"], name="idx_dihs_u_p_s_d"),
        ),
        migrations.AddIndex(
            model_name="dashboardinventoryhealthsummary",
            index=models.Index(fields=["user", "platform", "category"], name="idx_dihs_u_p_cat"),
        ),
        migrations.AddIndex(
            model_name="dashboardinventoryhealthsummary",
            index=models.Index(fields=["user", "platform", "portfolio"], name="idx_dihs_u_p_port"),
        ),
        migrations.AddIndex(
            model_name="dashboardinventoryhealthsummary",
            index=models.Index(fields=["user", "platform", "subcategory"], name="idx_dihs_u_p_sub"),
        ),
        migrations.AddIndex(
            model_name="dashboardinventoryhealthsummary",
            index=models.Index(fields=["user", "platform", "status"], name="idx_dihs_u_p_status"),
        ),
    ]
