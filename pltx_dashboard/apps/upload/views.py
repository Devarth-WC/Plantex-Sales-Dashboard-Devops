import os
import shutil
import tempfile
import logging
import json
from datetime import datetime
from uuid import uuid4
import re

from django.conf import settings
from django.core.cache import cache
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework.parsers import MultiPartParser, FormParser
from celery.result import AsyncResult

from apps.accounts.authentication import SessionUserIdAuthentication
from apps.accounts.utils import get_logged_in_user
from .models import UploadLog
from .schema import parse_sales_upload_date, validate_file_type
from .service_common import upload_batch_key
from .tasks import process_upload_file_task, _mark_batch_task_complete

logger = logging.getLogger(__name__)


# Flipkart file types for pipeline detection
FK_FILE_TYPES = {
    "fk_search_traffic",
    "fk_category",
    "fk_price",
    "fk_pla",
    "fk_fba_stock",
    "fk_inventory",
}

UPLOAD_TYPE_LABELS = {
    "sales": "Daily Sales",
    "category": "Category Mapping",
    "spend": "Ads Spends",
    "price": "Pricing Data",
    "fba_stock": "FBA Stock File",
    "flex_stock": "Flex Stock File",
    "fk_search_traffic": "FK Search Traffic",
    "fk_category": "FK Category",
    "fk_price": "FK Price",
    "fk_pla": "FK PLA",
    "fk_fba_stock": "FK FBA Stock File",
    "fk_inventory": "FK Inventory File",
}


UPLOAD_ROOT_DIR = os.getenv(
    "UPLOAD_ROOT_DIR", os.path.join(settings.BASE_DIR, "uploads")
)
UPLOAD_TMP_DIR = os.getenv(
    "UPLOAD_TMP_DIR", os.path.join(settings.BASE_DIR, "tmp")
)
ALLOWED_UPLOAD_EXTENSIONS = {".csv", ".xlsx", ".xls", ".xlsm"}

UPLOAD_SUBDIRS = {
    "sales": "sales",
    "category": "category",
    "spend": "spend",
    "price": "price",
    "fba_stock": "fba_stock",
    "flex_stock": "flex_stock",
    "fk_search_traffic": "traffic",
    "fk_category": "category",
    "fk_price": "price",
    "fk_pla": "pla",
    "fk_fba_stock": "fba_stock",
    "fk_inventory": "inventory",
}


def _track_upload_task(request, task_id):
    task_ids = request.session.get("upload_task_ids", [])
    if task_id not in task_ids:
        task_ids.append(task_id)
        request.session["upload_task_ids"] = task_ids[-500:]
        request.session.modified = True


def _is_allowed_upload_task(request, task_id):
    task_ids = request.session.get("upload_task_ids", [])
    return task_id in task_ids


def _get_upload_dir(file_type):
    platform_dir = "flipkart" if file_type in FK_FILE_TYPES else "amazon"
    category_dir = UPLOAD_SUBDIRS.get(file_type, "misc")
    upload_dir = os.path.join(UPLOAD_ROOT_DIR, platform_dir, category_dir)
    os.makedirs(upload_dir, exist_ok=True)
    return upload_dir


def _get_tmp_dir():
    """Return the staging tmp directory, creating it if needed."""
    os.makedirs(UPLOAD_TMP_DIR, exist_ok=True)
    return UPLOAD_TMP_DIR


def _save_upload_to_tmp(file_obj):
    """
    Save an uploaded file to the tmp/ staging directory.
    Returns the absolute path to the saved file.
    """
    tmp_dir = _get_tmp_dir()
    suffix = os.path.splitext(file_obj.name)[1] or ""
    ts = datetime.utcnow().strftime("%Y%m%d_%H%M%S_%f")
    unique_prefix = f"staged_{ts}_{uuid4().hex[:8]}_"
    fd, path = tempfile.mkstemp(suffix=suffix, prefix=unique_prefix, dir=tmp_dir)
    os.close(fd)

    temp_path_getter = getattr(file_obj, "temporary_file_path", None)
    if callable(temp_path_getter):
        source_path = temp_path_getter()
        shutil.move(source_path, path)
        return path

    with open(path, "wb") as f:
        for chunk in file_obj.chunks():
            f.write(chunk)
    return path


def _safe_original_name(name):
    """Return a safe basename for user-uploaded filenames."""
    name = os.path.basename(str(name or "")).strip()
    return name or "uploaded_file"


def _move_staged_to_upload_dir(staged_path, file_type, original_name=""):
    """
    Move a staged file from tmp/ to its proper uploads/ directory.
    Keep a readable name while preventing collisions.
    Returns the new absolute path.
    """
    upload_dir = _get_upload_dir(file_type)

    if original_name:
        clean_name = _safe_original_name(original_name)
        stem, ext = os.path.splitext(clean_name)
        ext = ext or os.path.splitext(staged_path)[1]
        ts = datetime.utcnow().strftime("%Y%m%d_%H%M%S_%f")
        filename = f"{ts}_{uuid4().hex[:8]}_{stem}{ext}"
    else:
        filename = os.path.basename(staged_path)

    dest_path = os.path.join(upload_dir, filename)
    shutil.move(staged_path, dest_path)

    # Defensive cleanup: shutil.move should remove source, but in edge cases
    # (filesystem quirks or interrupted copy+rename flows) ensure tmp source is gone.
    if os.path.exists(staged_path):
        try:
            os.remove(staged_path)
        except OSError:
            logger.warning(
                "[UploadMove] Staged source still present after move and cleanup failed: %s",
                staged_path,
            )
    return dest_path


def _register_upload_batch(batch_id, *, batch_total, user_id, data_owner_id, is_flipkart):
    ttl = 86400
    meta_key = upload_batch_key(batch_id, "meta")
    expected_key = upload_batch_key(batch_id, "expected_total")
    completed_key = upload_batch_key(batch_id, "completed_total")
    failed_key = upload_batch_key(batch_id, "failed_total")
    finalized_key = upload_batch_key(batch_id, "finalized")

    existing_expected = cache.get(expected_key)
    if existing_expected is not None and int(existing_expected) != int(batch_total):
        raise ValueError("Invalid batch_total for existing upload batch.")

    cache.set(
        meta_key,
        {
            "user_id": int(user_id),
            "data_owner_id": int(data_owner_id),
            "is_flipkart": bool(is_flipkart),
        },
        timeout=ttl,
    )
    cache.set(expected_key, int(batch_total), timeout=ttl)

    if existing_expected is None:
        cache.set(completed_key, 0, timeout=ttl)
        cache.set(failed_key, 0, timeout=ttl)
        cache.delete(finalized_key)


def _save_upload_to_disk(file_obj, file_type):
    """
    Save an uploaded file to a shared directory on disk
    so it can be passed to a Celery worker by path.
    Returns the absolute path to the saved file.
    """
    upload_dir = _get_upload_dir(file_type)
    suffix = os.path.splitext(file_obj.name)[1] or ""
    ts = datetime.utcnow().strftime("%Y%m%d_%H%M%S_%f")
    unique_prefix = f"upload_{ts}_{uuid4().hex[:8]}_"
    fd, path = tempfile.mkstemp(suffix=suffix, prefix=unique_prefix, dir=upload_dir)
    os.close(fd)

    temp_path_getter = getattr(file_obj, "temporary_file_path", None)
    if callable(temp_path_getter):
        source_path = temp_path_getter()
        shutil.move(source_path, path)
        return path

    with open(path, "wb") as f:
        for chunk in file_obj.chunks():
            f.write(chunk)
    return path


def _is_allowed_tabular_extension(filename):
    ext = os.path.splitext(str(filename or ""))[1].lower()
    return ext in ALLOWED_UPLOAD_EXTENSIONS


class StagedUploadView(APIView):
    """
    Phase 1: Upload files to tmp/ staging directory.
    Returns a staged_path identifier the client sends back in Phase 2.
    """
    parser_classes = (MultiPartParser, FormParser)
    authentication_classes = [SessionUserIdAuthentication]

    def post(self, request, *args, **kwargs):
        user = get_logged_in_user(request)
        if not user:
            return Response({"error": "Not authenticated"}, status=401)

        if not user.is_main_user:
            if (
                not user.role
                or not user.role.features.filter(code_name="upload_data").exists()
            ):
                return Response({"error": "Permission Denied"}, status=403)

        file_obj = request.FILES.get("file")
        file_type = request.data.get("file_type", "").strip()
        date_str = request.data.get("date", "")

        if not file_obj:
            return Response({"error": "file is required"}, status=400)

        if file_type:
            try:
                validate_file_type(file_type)
            except ValueError as exc:
                return Response({"error": str(exc)}, status=400)

        filename = os.path.basename(file_obj.name)
        if not _is_allowed_tabular_extension(filename):
            return Response(
                {"error": "Unsupported file format. Upload CSV or Excel (.xlsx/.xls/.xlsm)."},
                status=400,
            )

        # For sales files, validate date
        if file_type == "sales" and not date_str:
            date_str = os.path.splitext(filename)[0][:10]
        if file_type == "sales":
            try:
                parse_sales_upload_date(date_str)
            except ValueError as exc:
                return Response({"error": str(exc)}, status=400)

        # Save to tmp/ staging directory
        try:
            staged_path = _save_upload_to_tmp(file_obj)
        except Exception as e:
            logger.exception("[StagedUpload] Failed to save file to tmp: %s", e)
            return Response({"error": f"Failed to save file: {str(e)}"}, status=500)

        logger.info(
            "[StagedUpload] File staged: %s -> %s (type=%s)",
            filename, staged_path, file_type,
        )

        return Response(
            {
                "message": "File staged successfully",
                "staged_path": staged_path,
                "staged_tmp_dir": os.path.realpath(UPLOAD_TMP_DIR),
                "original_name": filename,
                "file_type": file_type,
                "date_str": date_str,
            },
            status=200,
        )


class StagedUploadDeleteView(APIView):
    """
    Delete a staged tmp file that hasn't been processed yet.
    """
    parser_classes = (MultiPartParser, FormParser)
    authentication_classes = [SessionUserIdAuthentication]

    def post(self, request, *args, **kwargs):
        user = get_logged_in_user(request)
        if not user:
            return Response({"error": "Not authenticated"}, status=401)

        if not user.is_main_user:
            if (
                not user.role
                or not user.role.features.filter(code_name="upload_data").exists()
            ):
                return Response({"error": "Permission Denied"}, status=403)

        staged_path = str(request.data.get("staged_path") or "").strip()
        if not staged_path:
            return Response({"error": "staged_path is required"}, status=400)

        real_staged = os.path.realpath(staged_path)
        real_tmp = os.path.realpath(UPLOAD_TMP_DIR)
        if not real_staged.startswith(real_tmp + os.sep):
            return Response({"error": "Invalid staged_path."}, status=400)

        if os.path.isfile(real_staged):
            try:
                os.remove(real_staged)
            except OSError as exc:
                return Response({"error": f"Failed to delete staged file: {exc}"}, status=500)

        return Response({"message": "Staged file removed."}, status=200)


class FileUploadView(APIView):
    """
    Phase 2: Process staged files. Accepts either:
    - A file upload directly (legacy), or
    - A staged_path from Phase 1 (moves from tmp/ to uploads/).
    """
    parser_classes = (MultiPartParser, FormParser)
    authentication_classes = [SessionUserIdAuthentication]

    def post(self, request, *args, **kwargs):
        user = get_logged_in_user(request)
        if not user:
            return Response({"error": "Not authenticated"}, status=401)

        # RBAC Check
        if not user.is_main_user:
            if (
                not user.role
                or not user.role.features.filter(code_name="upload_data").exists()
            ):
                return Response({"error": "Permission Denied"}, status=403)

        # Use data_owner (the main user) for data associations to avoid duplicates
        data_owner = user.created_by if user.created_by else user

        file_obj = request.FILES.get("file")
        staged_path = request.data.get("staged_path", "").strip()
        file_type = request.data.get("file_type")  # 'sales', 'spend', 'category', etc.
        date_str = request.data.get("date", "")
        batch_id = str(request.data.get("batch_id") or "").strip()
        batch_total_raw = request.data.get("batch_total")
        batch_total = None

        if not file_type:
            return Response({"error": "file_type is required"}, status=400)
        if not file_obj and not staged_path:
            return Response({"error": "file or staged_path is required"}, status=400)

        try:
            validate_file_type(file_type)
        except ValueError as exc:
            return Response({"error": str(exc)}, status=400)

        if batch_id:
            if not re.fullmatch(r"[A-Za-z0-9_-]{8,64}", batch_id):
                return Response({"error": "Invalid batch_id."}, status=400)
            try:
                batch_total = int(batch_total_raw)
            except (TypeError, ValueError):
                return Response({"error": "batch_total must be a positive integer."}, status=400)
            if batch_total <= 0:
                return Response({"error": "batch_total must be a positive integer."}, status=400)

        is_last = request.data.get("is_last") == "true"

        # Determine filename for logging
        if staged_path:
            filename = request.data.get("original_name", os.path.basename(staged_path))
        else:
            filename = os.path.basename(file_obj.name)

        if not _is_allowed_tabular_extension(filename):
            return Response(
                {"error": "Unsupported file format. Upload CSV or Excel (.xlsx/.xls/.xlsm)."},
                status=400,
            )

        if file_type == "sales" and not date_str:
            date_str = os.path.splitext(filename)[0][:10]
        if file_type == "sales":
            try:
                parse_sales_upload_date(date_str)
            except ValueError as exc:
                return Response({"error": str(exc)}, status=400)

        is_flipkart = file_type in FK_FILE_TYPES
        if batch_id:
            try:
                _register_upload_batch(
                    batch_id,
                    batch_total=batch_total,
                    user_id=user.id,
                    data_owner_id=data_owner.id,
                    is_flipkart=is_flipkart,
                )
            except ValueError as exc:
                return Response({"error": str(exc)}, status=400)

        # Resolve the file path: move from tmp/ to uploads/ or save directly
        try:
            if staged_path:
                # Validate the staged path is inside our tmp directory
                real_staged = os.path.realpath(staged_path)
                real_tmp = os.path.realpath(UPLOAD_TMP_DIR)
                if not real_staged.startswith(real_tmp + os.sep):
                    return Response({"error": "Invalid staged_path."}, status=400)
                if not os.path.isfile(real_staged):
                    return Response({"error": "Staged file not found. Please re-upload."}, status=404)
                # Move from tmp/ to proper uploads/ directory
                file_path = _move_staged_to_upload_dir(
                    real_staged,
                    file_type,
                    original_name=request.data.get("original_name", ""),
                )
            else:
                # Legacy: direct file upload (save to uploads/ directly)
                file_path = _save_upload_to_disk(file_obj, file_type)
        except Exception as e:
            return Response({"error": f"Failed to save file: {str(e)}"}, status=500)

        upload_log = UploadLog.objects.create(
            data_owner=data_owner,
            uploaded_by=user,
            file_name=filename,
            upload_type=UPLOAD_TYPE_LABELS.get(file_type, file_type),
            status=UploadLog.STATUS_QUEUED,
            message="Queued for processing.",
        )

        # Dispatch Celery task
        try:
            timeout = max(getattr(settings, "UPLOAD_TASK_TIMEOUT_SECONDS", 1800), 60)
            soft_timeout = max(timeout - 30, 60)
            task = process_upload_file_task.apply_async(
                kwargs={
                    "file_path": file_path,
                    "file_type": file_type,
                    "user_id": user.id,
                    "data_owner_id": data_owner.id,
                    "upload_log_id": upload_log.id,
                    "date_str": date_str,
                    "is_last": is_last,
                    "is_flipkart": is_flipkart,
                    "batch_id": batch_id,
                    "batch_total": batch_total,
                },
                time_limit=timeout,
                soft_time_limit=soft_timeout,
            )
        except Exception as exc:
            try:
                os.remove(file_path)
            except OSError:
                pass
            upload_log.status = UploadLog.STATUS_ERROR
            upload_log.message = f"Failed to queue task: {str(exc)}"
            upload_log.save(update_fields=["status", "message", "updated_at"])
            return Response({"error": "Failed to queue file for processing."}, status=500)

        cache.set(f"upload_task_owner_{task.id}", data_owner.id, timeout=86400)
        _track_upload_task(request, task.id)

        return Response(
            {
                "message": "File queued for processing",
                "task_id": task.id,
            },
            status=202,
        )


class BatchProcessStagedUploadView(APIView):
    """
    Phase 2 (batch): queue all staged files with a single click/request.
    Files are moved tmp/ -> uploads/ on server, then processing continues in background.
    """
    authentication_classes = [SessionUserIdAuthentication]

    def post(self, request, *args, **kwargs):
        user = get_logged_in_user(request)
        if not user:
            return Response({"error": "Not authenticated"}, status=401)

        if not user.is_main_user:
            if (
                not user.role
                or not user.role.features.filter(code_name="upload_data").exists()
            ):
                return Response({"error": "Permission Denied"}, status=403)

        data_owner = user.created_by if user.created_by else user

        staged_files = request.data.get("staged_files")
        if isinstance(staged_files, str):
            try:
                staged_files = json.loads(staged_files)
            except json.JSONDecodeError:
                return Response({"error": "Invalid staged_files payload."}, status=400)
        if not isinstance(staged_files, list) or not staged_files:
            return Response({"error": "staged_files must be a non-empty list."}, status=400)

        batch_id = str(request.data.get("batch_id") or "").strip() or uuid4().hex
        if not re.fullmatch(r"[A-Za-z0-9_-]{8,64}", batch_id):
            return Response({"error": "Invalid batch_id."}, status=400)

        prepared = []
        real_tmp = os.path.realpath(UPLOAD_TMP_DIR)

        for idx, item in enumerate(staged_files, start=1):
            if not isinstance(item, dict):
                return Response({"error": f"staged_files[{idx}] must be an object."}, status=400)

            staged_path = str(item.get("staged_path") or "").strip()
            file_type = str(item.get("file_type") or "").strip()
            original_name = str(item.get("original_name") or "").strip()
            date_str = str(item.get("date_str") or "").strip()

            if not staged_path:
                return Response({"error": f"staged_files[{idx}].staged_path is required."}, status=400)
            if not file_type:
                return Response({"error": f"staged_files[{idx}].file_type is required."}, status=400)

            try:
                validate_file_type(file_type)
            except ValueError as exc:
                return Response({"error": str(exc)}, status=400)

            filename = original_name or os.path.basename(staged_path)
            if not _is_allowed_tabular_extension(filename):
                return Response(
                    {
                        "error": (
                            f"Unsupported file format for '{filename}'. "
                            "Upload CSV or Excel (.xlsx/.xls/.xlsm)."
                        )
                    },
                    status=400,
                )

            if file_type == "sales" and not date_str:
                date_str = os.path.splitext(filename)[0][:10]
            if file_type == "sales":
                try:
                    parse_sales_upload_date(date_str)
                except ValueError as exc:
                    return Response({"error": str(exc)}, status=400)

            real_staged = os.path.realpath(staged_path)
            if not real_staged.startswith(real_tmp + os.sep):
                return Response({"error": "Invalid staged_path."}, status=400)
            if not os.path.isfile(real_staged):
                return Response(
                    {"error": f"Staged file not found for '{filename}'. Please re-upload."},
                    status=404,
                )

            prepared.append(
                {
                    "real_staged": real_staged,
                    "file_type": file_type,
                    "filename": filename,
                    "date_str": date_str,
                    "is_flipkart": file_type in FK_FILE_TYPES,
                }
            )

        batch_total = len(prepared)
        batch_is_flipkart = prepared[0]["is_flipkart"]
        if any(it["is_flipkart"] != batch_is_flipkart for it in prepared):
            return Response(
                {"error": "Cannot process Amazon and Flipkart files in the same batch."},
                status=400,
            )

        try:
            _register_upload_batch(
                batch_id,
                batch_total=batch_total,
                user_id=user.id,
                data_owner_id=data_owner.id,
                is_flipkart=batch_is_flipkart,
            )
        except ValueError as exc:
            return Response({"error": str(exc)}, status=400)

        timeout = max(getattr(settings, "UPLOAD_TASK_TIMEOUT_SECONDS", 1800), 60)
        soft_timeout = max(timeout - 30, 60)

        queued_count = 0
        failed_count = 0
        task_ids = []
        failures = []

        for item in prepared:
            file_type = item["file_type"]
            filename = item["filename"]

            upload_log = UploadLog.objects.create(
                data_owner=data_owner,
                uploaded_by=user,
                file_name=filename,
                upload_type=UPLOAD_TYPE_LABELS.get(file_type, file_type),
                status=UploadLog.STATUS_QUEUED,
                message="Queued for processing.",
            )

            try:
                file_path = _move_staged_to_upload_dir(
                    item["real_staged"],
                    file_type,
                    original_name=filename,
                )
            except Exception as exc:
                failed_count += 1
                failures.append({"file_name": filename, "error": str(exc)})
                upload_log.status = UploadLog.STATUS_ERROR
                upload_log.message = f"Failed to move staged file: {str(exc)}"
                upload_log.save(update_fields=["status", "message", "updated_at"])
                _mark_batch_task_complete(
                    batch_id=batch_id,
                    batch_total=batch_total,
                    user_id=user.id,
                    data_owner_id=data_owner.id,
                    is_flipkart=batch_is_flipkart,
                    success=False,
                    file_type=file_type,
                )
                continue

            try:
                task = process_upload_file_task.apply_async(
                    kwargs={
                        "file_path": file_path,
                        "file_type": file_type,
                        "user_id": user.id,
                        "data_owner_id": data_owner.id,
                        "upload_log_id": upload_log.id,
                        "date_str": item["date_str"],
                        "is_last": False,
                        "is_flipkart": item["is_flipkart"],
                        "batch_id": batch_id,
                        "batch_total": batch_total,
                    },
                    time_limit=timeout,
                    soft_time_limit=soft_timeout,
                )
            except Exception as exc:
                failed_count += 1
                failures.append({"file_name": filename, "error": str(exc)})
                try:
                    os.remove(file_path)
                except OSError:
                    pass
                upload_log.status = UploadLog.STATUS_ERROR
                upload_log.message = f"Failed to queue task: {str(exc)}"
                upload_log.save(update_fields=["status", "message", "updated_at"])
                _mark_batch_task_complete(
                    batch_id=batch_id,
                    batch_total=batch_total,
                    user_id=user.id,
                    data_owner_id=data_owner.id,
                    is_flipkart=batch_is_flipkart,
                    success=False,
                    file_type=file_type,
                )
                continue

            queued_count += 1
            task_ids.append(task.id)
            cache.set(f"upload_task_owner_{task.id}", data_owner.id, timeout=86400)
            _track_upload_task(request, task.id)

        status_code = 202 if queued_count > 0 else 500
        message = (
            "Files queued for background processing."
            if failed_count == 0
            else "Some files failed to queue. Please check upload logs."
        )
        return Response(
            {
                "message": message,
                "batch_id": batch_id,
                "batch_total": batch_total,
                "queued_count": queued_count,
                "failed_count": failed_count,
                "task_ids": task_ids,
                "failures": failures,
            },
            status=status_code,
        )


class UploadTaskStatusView(APIView):
    """Poll Celery task state for an upload processing task."""

    authentication_classes = [SessionUserIdAuthentication]

    def get(self, request, task_id, *args, **kwargs):
        user = get_logged_in_user(request)
        if not user:
            return Response({"error": "Not authenticated"}, status=401)

        if not _is_allowed_upload_task(request, task_id):
            return Response({"error": "Permission Denied"}, status=403)

        data_owner = user.created_by if user.created_by else user
        task_owner_id = cache.get(f"upload_task_owner_{task_id}")
        if task_owner_id is None:
            return Response({"error": "Task not found or expired"}, status=404)
        if int(task_owner_id) != int(data_owner.id):
            return Response({"error": "Permission Denied"}, status=403)

        task = AsyncResult(task_id)

        if task.state == "PENDING":
            return Response({"status": "processing", "state": "PENDING"})
        elif task.state == "SUCCESS":
            result = task.result or {}
            return Response(
                {
                    "status": result.get("status", "success"),
                    "file_type": result.get("file_type", ""),
                    "is_last": result.get("is_last", False),
                    "message": result.get("message", ""),
                }
            )
        elif task.state == "FAILURE":
            return Response(
                {
                    "status": "error",
                    "message": str(task.info),
                },
                status=500,
            )
        else:
            return Response({"status": "processing", "state": task.state})
