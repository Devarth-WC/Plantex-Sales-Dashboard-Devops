"""
Add date field to FlipkartInventoryStock and update unique_together
to (user, fsn, date) for FSN+Date level granularity.
"""

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("dashboard", "0024_flipkartfba"),
    ]

    operations = [
        # 1. Add the date column as nullable first
        migrations.AddField(
            model_name="flipkartinventorystock",
            name="date",
            field=models.DateField(db_index=True, null=True),
            preserve_default=False,
        ),
        # 2. Backfill existing rows with today's date
        migrations.RunSQL(
            sql="UPDATE dashboard_flipkartinventorystock SET date = CURDATE() WHERE date IS NULL;",
            reverse_sql=migrations.RunSQL.noop,
        ),
        # 3. Make it NOT NULL
        migrations.AlterField(
            model_name="flipkartinventorystock",
            name="date",
            field=models.DateField(db_index=True),
        ),
        # 4. Update unique_together
        migrations.AlterUniqueTogether(
            name="flipkartinventorystock",
            unique_together={("user", "fsn", "date")},
        ),
        # 5. Add indexes
        migrations.AddIndex(
            model_name="flipkartinventorystock",
            index=models.Index(
                fields=["user", "date", "fsn"], name="idx_fkinv_u_d_fsn"
            ),
        ),
        migrations.AddIndex(
            model_name="flipkartinventorystock",
            index=models.Index(
                fields=["user", "fsn"], name="idx_fkinv_u_fsn"
            ),
        ),
    ]
