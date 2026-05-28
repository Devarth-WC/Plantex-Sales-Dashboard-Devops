from django.contrib import admin

from .models import UploadLog


@admin.register(UploadLog)
class UploadLogAdmin(admin.ModelAdmin):
    list_display = (
        "created_at",
        "file_name",
        "upload_type",
        "status",
        "uploaded_by",
        "data_owner",
    )
    list_filter = ("status", "upload_type", "created_at")
    search_fields = ("file_name", "upload_type", "uploaded_by__email", "data_owner__email")
    readonly_fields = (
        "data_owner",
        "uploaded_by",
        "file_name",
        "upload_type",
        "status",
        "message",
        "created_at",
        "updated_at",
    )

    def has_add_permission(self, request):
        return False
