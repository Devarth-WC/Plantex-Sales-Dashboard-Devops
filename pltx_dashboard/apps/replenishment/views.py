import os
import pandas as pd
import tempfile
from datetime import datetime
from io import BytesIO
from django.shortcuts import render, redirect
from django.http import JsonResponse, FileResponse
from celery.result import AsyncResult
from apps.accounts.utils import get_logged_in_user
from apps.accounts.models import Feature

# Logic imports
from .tasks import validate_reports_celery, generate_master_celery

from apps.accounts.decorators import require_feature

ALLOWED_UPLOAD_EXTENSIONS = {".csv", ".xlsx", ".xls", ".xlsm"}


def _require_replenishment_api_user(request):
    user = get_logged_in_user(request)
    if not user:
        return None, None, JsonResponse({"error": "Not authenticated"}, status=401)

    if user.is_main_user:
        data_owner = user
        return user, data_owner, None

    if not user.role or not user.role.features.filter(code_name="replenishment").exists():
        return None, None, JsonResponse({"error": "Permission denied"}, status=403)

    data_owner = user.created_by if user.created_by else user
    return user, data_owner, None


def _track_replenishment_task(request, task_id):
    task_ids = request.session.get("replenishment_task_ids", [])
    if task_id not in task_ids:
        task_ids.append(task_id)
        request.session["replenishment_task_ids"] = task_ids[-200:]
        request.session.modified = True


def _is_allowed_replenishment_task(request, task_id):
    task_ids = request.session.get("replenishment_task_ids", [])
    return task_id in task_ids


def _path_is_allowed_for_master_download(request, file_path):
    if not file_path:
        return False

    master_data = request.session.get("master_report") or {}
    allowed_paths = [master_data.get("csv_path"), master_data.get("excel_path")]
    allowed_real_paths = {
        os.path.realpath(os.path.abspath(path)) for path in allowed_paths if path
    }
    file_real_path = os.path.realpath(os.path.abspath(file_path))
    return file_real_path in allowed_real_paths


@require_feature("replenishment")
def index(request):
    user = get_logged_in_user(request)
    if not user:
        return redirect("account-login")

    if user.is_main_user:
        user_features = [f.code_name for f in Feature.objects.all()]
    else:
        user_features = (
            [f.code_name for f in user.role.features.all()] if user.role else []
        )

    context = {
        "logged_user": user,
        "user_features": user_features,
        "page_title": "Replenishment Report Generator",
        "payload": {
            "filters": {
                "platforms": ["Amazon", "Flipkart"],
                "categories": [],
                "asins": [],
            }
        },
        "selected_filters": {"categories": [], "asins": []},
    }
    return render(request, "replenishment/index.html", context)


def save_uploaded_files(request_files):
    """Save multi-part uploaded files to a system temporary directory."""
    temp_dir = tempfile.mkdtemp(prefix="repl_uploads_")
    saved_paths = {}
    for key, file in request_files.items():
        # Sanitize filename
        safe_name = "".join(
            [c for c in file.name if c.isalnum() or c in "._- "]
        ).strip()
        if not safe_name:
            safe_name = "uploaded_file"
        path = os.path.join(temp_dir, safe_name)
        with open(path, "wb+") as destination:
            for chunk in file.chunks():
                destination.write(chunk)
        saved_paths[key] = path
    return saved_paths


def _validate_uploaded_extensions(request_files):
    invalid = []
    for key, file in request_files.items():
        ext = os.path.splitext(str(getattr(file, "name", "") or ""))[1].lower()
        if ext not in ALLOWED_UPLOAD_EXTENSIONS:
            invalid.append(f"{key} ({ext or 'no extension'})")
    if invalid:
        raise ValueError(
            "Unsupported file format for: "
            + ", ".join(invalid)
            + ". Upload CSV or Excel (.xlsx/.xls/.xlsm)."
        )


def validate_api(request):
    try:
        _, _, auth_error = _require_replenishment_api_user(request)
        if auth_error:
            return auth_error

        if request.method != "POST":
            return JsonResponse({"error": "Only POST allowed"}, status=405)

        if not request.FILES:
            return JsonResponse({"error": "No files uploaded"}, status=400)
        _validate_uploaded_extensions(request.FILES)

        # Save files to a unique temporary folder
        files = save_uploaded_files(request.FILES)

        reports_to_validate = [
            ("Sales", files.get("Sales")),
            ("Shipment", files.get("Shipment")),
            ("Stock", files.get("Stock")),
            ("LIS", files.get("LIS")),
        ]

        # Needs Assortment for validation logic
        if not files.get("Assortment") or not os.path.exists(files["Assortment"]):
            return JsonResponse(
                {"error": "Assortment Master file is required for validation"},
                status=400,
            )

        # Pass mapping file paths to Celery — master_data is generated inside the task
        mapping_files = {
            "FC_Cluster": files.get("FC_Cluster"),
            "Pincode_Cluster": files.get("Pincode_Cluster"),
            "Assortment": files.get("Assortment"),
            "Input_Sheet": files.get("Input_Sheet"),
        }

        task = validate_reports_celery.delay(reports_to_validate, mapping_files)
        _track_replenishment_task(request, task.id)

        return JsonResponse({"task_id": task.id, "status": "processing"})
    except Exception as e:
        return JsonResponse({"error": f"Validation Error: {str(e)}"}, status=500)


def generate_master_api(request):
    import traceback
    import logging

    logger = logging.getLogger(__name__)

    try:
        _, _, auth_error = _require_replenishment_api_user(request)
        if auth_error:
            return auth_error

        if request.method != "POST":
            return JsonResponse({"error": "Only POST allowed"}, status=405)

        if not request.FILES:
            return JsonResponse({"error": "No files uploaded"}, status=400)
        _validate_uploaded_extensions(request.FILES)

        logger.info(
            f"[generate_master_api] Received {len(request.FILES)} files: {list(request.FILES.keys())}"
        )

        # Save files to a unique temporary folder
        files = save_uploaded_files(request.FILES)

        required = [
            "Sales",
            "Stock",
            "LIS",
            "Shipment",
            "Assortment",
            "FC_Cluster",
            "Pincode_Cluster",
            "Input_Sheet",
            "Business_Report",
        ]
        missing = [
            req
            for req in required
            if not files.get(req) or not os.path.exists(files[req])
        ]
        if missing:
            logger.warning(f"[generate_master_api] Missing files: {missing}")
            return JsonResponse(
                {"error": f"Missing uploaded files for: {', '.join(missing)}"},
                status=400,
            )

        # Flex Qty is optional
        logger.info(
            f"[generate_master_api] Flex Qty file provided: {'Flex_Qty' in files and files['Flex_Qty'] and os.path.exists(files['Flex_Qty'])}"
        )

        temp_dir = tempfile.mkdtemp()
        task = generate_master_celery.delay(files, temp_dir)
        _track_replenishment_task(request, task.id)
        logger.info(f"[generate_master_api] Celery task dispatched: {task.id}")

        return JsonResponse({"task_id": task.id, "status": "processing"})
    except Exception as e:
        logger.error(f"[generate_master_api] Error: {str(e)}\n{traceback.format_exc()}")
        return JsonResponse({"error": f"Generation Error: {str(e)}"}, status=500)


def check_task_status(request, task_id):
    """Poll Celery state and transfer result to session so downloads work"""
    _, _, auth_error = _require_replenishment_api_user(request)
    if auth_error:
        return auth_error

    if not _is_allowed_replenishment_task(request, task_id):
        return JsonResponse({"error": "Permission denied"}, status=403)

    task = AsyncResult(task_id)

    if task.state == "PENDING":
        return JsonResponse({"status": "processing", "state": "queued"})

    elif task.state == "STARTED":
        # Task is actively running in a Celery worker
        return JsonResponse({"status": "processing", "state": "running"})

    elif task.state == "SUCCESS":
        task_data = task.result
        if not task_data:
            return JsonResponse({"error": "Task returned empty result"}, status=500)

        if task_data.get("status") == "error":
            return JsonResponse(
                {
                    "status": "error",
                    "message": task_data.get("message", "Unknown processing error."),
                }
            )

        task_type = task_data.get("task_type")
        if task_type == "validation":
            if "error_data_map" in task_data:
                request.session["validation_errors"] = task_data["error_data_map"]
                request.session.modified = True
            return JsonResponse(
                {
                    "status": "success",
                    "total_errors": task_data.get("total_errors", 0),
                    "reports": task_data.get("reports", {}),
                }
            )
        elif task_type == "generation":
            if "csv_path" in task_data:
                request.session["master_report"] = {
                    "csv_path": task_data["csv_path"],
                    "excel_path": task_data.get("excel_path"),
                    "temp_dir": task_data["temp_dir"],
                }
                request.session.modified = True
            return JsonResponse(
                {
                    "status": "success",
                    "message": "Master report generated successfully.",
                }
            )

    elif task.state == "FAILURE":
        return JsonResponse({"status": "error", "message": str(task.info)})

    return JsonResponse({"status": "processing", "state": task.state})


def download_validation_error(request, report_type, file_format):
    """Download validation error file on-demand"""
    _, _, auth_error = _require_replenishment_api_user(request)
    if auth_error:
        return auth_error

    if (
        "validation_errors" not in request.session
        or report_type not in request.session["validation_errors"]
    ):
        return JsonResponse({"error": "Validation data not found"}, status=404)

    error_data = request.session["validation_errors"][report_type]
    df_errors = pd.DataFrame(error_data["data"])

    if file_format == "csv":
        output = BytesIO()
        df_errors.to_csv(output, index=False)
        output.seek(0)
        response = FileResponse(output, content_type="text/csv")
        response["Content-Disposition"] = (
            f'attachment; filename="{report_type}_validation_errors_{datetime.now().strftime("%Y%m%d_%H%M%S")}.csv"'
        )
        return response
    elif file_format == "excel":
        output = BytesIO()
        with pd.ExcelWriter(output, engine="openpyxl") as writer:
            df_errors.to_excel(writer, sheet_name="Errors", index=False)
        output.seek(0)
        response = FileResponse(
            output,
            content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )
        response["Content-Disposition"] = (
            f'attachment; filename="{report_type}_validation_errors_{datetime.now().strftime("%Y%m%d_%H%M%S")}.xlsx"'
        )
        return response
    else:
        return JsonResponse({"error": "Invalid format"}, status=400)


def download_master_report(request, file_format):
    """Download master report file on-demand"""
    _, _, auth_error = _require_replenishment_api_user(request)
    if auth_error:
        return auth_error

    if "master_report" not in request.session:
        return JsonResponse({"error": "Master report not found"}, status=404)

    master_data = request.session["master_report"]

    try:
        if file_format == "csv":
            csv_path = master_data.get("csv_path")
            if not _path_is_allowed_for_master_download(request, csv_path):
                return JsonResponse({"error": "Permission denied"}, status=403)
            if not os.path.exists(csv_path):
                return JsonResponse({"error": "CSV file not found"}, status=404)
            response = FileResponse(
                open(csv_path, "rb"), content_type="text/csv"
            )
            response["Content-Disposition"] = (
                f'attachment; filename="Master_Merged_Report_{datetime.now().strftime("%Y%m%d_%H%M%S")}.csv"'
            )
            return response
        elif file_format == "excel":
            excel_path = master_data.get("excel_path")
            if not _path_is_allowed_for_master_download(request, excel_path):
                return JsonResponse({"error": "Permission denied"}, status=403)
            if not excel_path or not os.path.exists(excel_path):
                return JsonResponse({"error": "Excel file not found"}, status=404)
            response = FileResponse(
                open(excel_path, "rb"),
                content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            )
            response["Content-Disposition"] = (
                f'attachment; filename="Master_Merged_Report_{datetime.now().strftime("%Y%m%d_%H%M%S")}.xlsx"'
            )
            return response
        else:
            return JsonResponse({"error": "Invalid format"}, status=400)
    except Exception as e:
        return JsonResponse({"error": str(e)}, status=500)


def download_file(request):
    _, _, auth_error = _require_replenishment_api_user(request)
    if auth_error:
        return auth_error

    filepath = request.GET.get("path")
    if not filepath:
        return JsonResponse({"error": "File not found"}, status=404)
    if not _path_is_allowed_for_master_download(request, filepath):
        return JsonResponse({"error": "Permission denied"}, status=403)
    if not os.path.exists(filepath):
        return JsonResponse({"error": "File not found"}, status=404)

    return FileResponse(open(filepath, "rb"), as_attachment=True)
