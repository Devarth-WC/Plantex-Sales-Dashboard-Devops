from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("dashboard", "0030_add_fkpla_user_date_index"),
    ]

    operations = [
        migrations.AddIndex(
            model_name="spenddata",
            index=models.Index(
                fields=["user", "date"],
                name="idx_spend_u_date",
            ),
        ),
    ]
