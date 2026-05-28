from django.urls import path
from .views import (
    BatchProcessStagedUploadView,
    FileUploadView,
    StagedUploadDeleteView,
    StagedUploadView,
    UploadTaskStatusView,
)

urlpatterns = [
    path("api/upload/stage/", StagedUploadView.as_view(), name="api-upload-stage"),
    path(
        "api/upload/stage/delete/",
        StagedUploadDeleteView.as_view(),
        name="api-upload-stage-delete",
    ),
    path(
        "api/upload/process-batch/",
        BatchProcessStagedUploadView.as_view(),
        name="api-upload-process-batch",
    ),
    path("api/upload/", FileUploadView.as_view(), name="api-upload"),
    path(
        "api/upload/status/<str:task_id>/",
        UploadTaskStatusView.as_view(),
        name="api-upload-status",
    ),
]
