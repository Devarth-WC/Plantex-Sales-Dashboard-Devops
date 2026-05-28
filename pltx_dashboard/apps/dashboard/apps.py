from django.apps import AppConfig


class DashboardConfig(AppConfig):
    name = "apps.dashboard"

    def ready(self):
        from . import signals  # noqa: F401
