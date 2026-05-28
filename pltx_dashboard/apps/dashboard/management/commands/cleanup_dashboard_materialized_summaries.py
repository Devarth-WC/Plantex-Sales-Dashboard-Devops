from django.conf import settings
from django.core.management.base import BaseCommand

from apps.dashboard.services.materialized_cache import cleanup_materialized_summaries


class Command(BaseCommand):
    help = "Cleanup old/overflow rows from DashboardMaterializedSummary."

    def add_arguments(self, parser):
        parser.add_argument(
            "--days",
            type=int,
            default=getattr(settings, "DASHBOARD_SUMMARY_RETENTION_DAYS", 14),
            help="Retention window in days.",
        )
        parser.add_argument(
            "--max-rows-per-view",
            type=int,
            default=getattr(settings, "DASHBOARD_SUMMARY_MAX_ROWS_PER_VIEW", 800),
            help="Maximum retained rows per (user, view_type).",
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Show cleanup stats without deleting rows.",
        )

    def handle(self, *args, **options):
        stats = cleanup_materialized_summaries(
            retention_days=options["days"],
            max_rows_per_view=options["max_rows_per_view"],
            dry_run=options["dry_run"],
        )
        self.stdout.write(self.style.SUCCESS(str(stats)))
