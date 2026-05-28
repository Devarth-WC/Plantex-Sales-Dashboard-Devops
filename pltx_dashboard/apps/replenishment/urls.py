from django.urls import path
from . import views

urlpatterns = [
    path("replenishment/", views.index, name="replenishment-index"),
    path(
        "api/replenishment/validate/", views.validate_api, name="replenishment-validate"
    ),
    path(
        "api/replenishment/generate_master/",
        views.generate_master_api,
        name="replenishment-generate-master",
    ),
    path(
        "api/replenishment/status/<str:task_id>/",
        views.check_task_status,
        name="replenishment-status",
    ),
    path(
        "api/replenishment/download/validation/<str:report_type>/<str:file_format>/",
        views.download_validation_error,
        name="replenishment-download-validation",
    ),
    path(
        "api/replenishment/download/master/<str:file_format>/",
        views.download_master_report,
        name="replenishment-download-master",
    ),
    path(
        "api/replenishment/download/",
        views.download_file,
        name="replenishment-download-file",
    ),
]
