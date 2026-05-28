from django.contrib import admin
from django.urls import path, include
from django.conf import settings
from django.conf.urls.static import static
from django.contrib.staticfiles.urls import staticfiles_urlpatterns
from django.http import JsonResponse
import logging

logger = logging.getLogger(__name__)


# Silences the automatic Chrome DevTools probe — returns {} instead of a 404.
def _chrome_devtools_noop(request):
    return JsonResponse({})


urlpatterns = [
    path("admin/", admin.site.urls),
    path(".well-known/appspecific/com.chrome.devtools.json", _chrome_devtools_noop),
    path("accounts/", include("apps.accounts.urls")),
    path("", include("apps.upload.urls")),
    path("", include("apps.dashboard.urls")),
    path("", include("apps.replenishment.urls")),
]

if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
    urlpatterns += staticfiles_urlpatterns()


# ── Custom error handlers that return JSON for API requests ──
def _is_api_request(request):
    """Check if the request is an API/AJAX call expecting JSON."""
    if request.path.startswith("/api/"):
        return True
    if request.headers.get("X-Requested-With") == "XMLHttpRequest":
        return True
    accept = request.headers.get("Accept", "")
    if "application/json" in accept:
        return True
    return False


def custom_bad_request(request, exception=None):
    if _is_api_request(request):
        return JsonResponse({"error": f"Bad Request: {exception}"}, status=400)
    from django.views.defaults import bad_request

    return bad_request(request, exception)


def custom_permission_denied(request, exception=None):
    if _is_api_request(request):
        return JsonResponse(
            {"error": "Permission Denied (CSRF or auth failure)"}, status=403
        )
    from django.views.defaults import permission_denied

    return permission_denied(request, exception)


def custom_server_error(request):
    if _is_api_request(request):
        return JsonResponse({"error": "Internal Server Error"}, status=500)
    from django.views.defaults import server_error

    return server_error(request)


handler400 = custom_bad_request
handler403 = custom_permission_denied
handler500 = custom_server_error
