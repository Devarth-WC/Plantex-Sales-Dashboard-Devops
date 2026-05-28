import logging
import re
import time
from contextlib import ExitStack

from django.conf import settings
from django.db import connections

logger = logging.getLogger("apps.dashboard.query_profiler")

_WHITESPACE_RE = re.compile(r"\s+")


class _QueryCollector:
    def __init__(self):
        self.entries = []

    def __call__(self, execute, sql, params, many, context):
        started = time.perf_counter()
        try:
            return execute(sql, params, many, context)
        finally:
            elapsed_ms = (time.perf_counter() - started) * 1000.0
            self.entries.append(
                {
                    "duration_ms": elapsed_ms,
                    "sql": str(sql or ""),
                    "many": bool(many),
                }
            )


class DashboardQueryProfilingMiddleware:
    """
    Profile DB queries for dashboard endpoints and log:
    - every query slower than configured threshold
    - top N most expensive SQL calls for slow requests
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if not self._is_enabled() or not self._should_profile(request.path):
            return self.get_response(request)

        collector = _QueryCollector()
        started = time.perf_counter()

        with ExitStack() as stack:
            for connection in connections.all():
                stack.enter_context(connection.execute_wrapper(collector))
            response = self.get_response(request)

        request_ms = (time.perf_counter() - started) * 1000.0
        self._emit_logs(request, request_ms, collector.entries)
        return response

    @staticmethod
    def _is_enabled():
        return bool(getattr(settings, "DASHBOARD_QUERY_PROFILING_ENABLED", True))

    @staticmethod
    def _should_profile(path):
        return str(path or "").startswith(("/dashboard/", "/api/dashboard/"))

    def _emit_logs(self, request, request_ms, entries):
        if not entries:
            return

        slow_query_ms = float(getattr(settings, "DASHBOARD_SLOW_QUERY_MS", 200))
        slow_endpoint_ms = float(getattr(settings, "DASHBOARD_SLOW_ENDPOINT_MS", 800))
        top_n = int(getattr(settings, "DASHBOARD_TOP_EXPENSIVE_QUERIES", 5))
        top_n = max(top_n, 1)

        total_db_ms = sum(float(e.get("duration_ms") or 0.0) for e in entries)
        slow_entries = [
            e for e in entries if float(e.get("duration_ms") or 0.0) >= slow_query_ms
        ]
        top_entries = sorted(
            entries,
            key=lambda e: float(e.get("duration_ms") or 0.0),
            reverse=True,
        )[:top_n]

        endpoint = f"{request.method} {request.path}"
        for entry in slow_entries:
            logger.warning(
                "[SlowQuery] endpoint=%s duration_ms=%.1f sql=%s",
                endpoint,
                float(entry.get("duration_ms") or 0.0),
                self._sql_snippet(entry.get("sql")),
            )

        should_log_summary = bool(slow_entries) or request_ms >= slow_endpoint_ms
        if not should_log_summary:
            return

        summary_parts = [
            f"{float(e.get('duration_ms') or 0.0):.1f}ms :: {self._sql_snippet(e.get('sql'))}"
            for e in top_entries
        ]
        logger.warning(
            "[QueryProfile] endpoint=%s request_ms=%.1f db_ms=%.1f query_count=%d slow_query_count=%d top_%d=%s",
            endpoint,
            request_ms,
            total_db_ms,
            len(entries),
            len(slow_entries),
            top_n,
            " || ".join(summary_parts),
        )

    @staticmethod
    def _sql_snippet(sql):
        compact = _WHITESPACE_RE.sub(" ", str(sql or "")).strip()
        if len(compact) > 320:
            return compact[:317] + "..."
        return compact
