from django.core.files.storage import FileSystemStorage
from django.db import models
import os


# These are kept ONLY for historical migration compatibility
class OverwriteStorage(FileSystemStorage):
    def get_available_name(self, name, _max_length=None):
        if self.exists(name):
            os.remove(os.path.join(self.location, name))
        return name


# Required by historical migration 0002_alter_uploadedfile_file.
def user_directory_path(instance, filename):
    return f"uploads/{instance.user.id}/{filename}"


class UploadLog(models.Model):
    STATUS_QUEUED = "queued"
    STATUS_PROCESSING = "processing"
    STATUS_SUCCESS = "success"
    STATUS_ERROR = "error"
    STATUS_CHOICES = [
        (STATUS_QUEUED, "Queued"),
        (STATUS_PROCESSING, "Processing"),
        (STATUS_SUCCESS, "Success"),
        (STATUS_ERROR, "Error"),
    ]

    data_owner = models.ForeignKey(
        "accounts.Users", on_delete=models.CASCADE, related_name="upload_logs"
    )
    uploaded_by = models.ForeignKey(
        "accounts.Users", on_delete=models.CASCADE, related_name="uploaded_file_logs"
    )
    file_name = models.CharField(max_length=255)
    upload_type = models.CharField(max_length=100)
    status = models.CharField(
        max_length=20, choices=STATUS_CHOICES, default=STATUS_QUEUED, db_index=True
    )
    message = models.TextField(blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["data_owner", "created_at"], name="idx_uplog_owner_dt"),
            models.Index(fields=["data_owner", "status"], name="idx_uplog_owner_st"),
        ]

    def __str__(self):
        return f"{self.file_name} ({self.upload_type}) - {self.status}"
