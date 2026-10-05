from __future__ import annotations

import json
import logging
import time
from uuid import uuid4

from fastapi import Request, Response
from prometheus_client import CONTENT_TYPE_LATEST, REGISTRY, Counter, Histogram, generate_latest
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint

logger = logging.getLogger("recommendation_platform.http")

HTTP_REQUESTS = Counter(
    "recommendation_http_requests_total",
    "HTTP requests completed by the API.",
    ("method", "route", "status"),
)
HTTP_REQUEST_DURATION = Histogram(
    "recommendation_http_request_duration_seconds",
    "HTTP request duration in seconds.",
    ("method", "route"),
)
CACHE_OPERATIONS = Counter(
    "recommendation_cache_operations_total",
    "Recommendation cache operations by outcome.",
    ("operation", "outcome"),
)
RANKING_REQUESTS = Counter(
    "recommendation_ranking_requests_total",
    "Recommendation requests by strategy, model, and result source.",
    ("strategy", "model_version", "source"),
)
RANKING_RESULTS = Counter(
    "recommendation_ranking_results_total",
    "Individual recommendations returned by strategy and source.",
    ("strategy", "source"),
)
INTERACTION_INGESTIONS = Counter(
    "recommendation_interaction_ingestions_total",
    "Interaction ingestion attempts by durable outcome.",
    ("outcome",),
)
IMPRESSIONS_RECORDED = Counter(
    "recommendation_impressions_recorded_total",
    "Recommendation impressions durably recorded by strategy and ranking source.",
    ("strategy", "source"),
)

_UNMEASURED_PATHS = frozenset({"/health", "/ready", "/metrics"})


class HttpObservabilityMiddleware(BaseHTTPMiddleware):
    """Add request correlation and bounded-cardinality HTTP telemetry."""

    async def dispatch(
        self, request: Request, call_next: RequestResponseEndpoint
    ) -> Response:
        request_id = str(uuid4())
        started = time.perf_counter()
        status_code = 500

        try:
            response = await call_next(request)
            status_code = response.status_code
        except Exception:
            self._observe(request, request_id, status_code, started)
            raise

        response.headers["X-Request-ID"] = request_id
        self._observe(request, request_id, status_code, started)
        return response

    @staticmethod
    def _observe(request: Request, request_id: str, status_code: int, started: float) -> None:
        duration = time.perf_counter() - started
        route = getattr(request.scope.get("route"), "path", "unmatched")

        if request.url.path not in _UNMEASURED_PATHS:
            HTTP_REQUESTS.labels(request.method, route, str(status_code)).inc()
            HTTP_REQUEST_DURATION.labels(request.method, route).observe(duration)

        logger.info(
            json.dumps(
                {
                    "event": "http_request_completed",
                    "request_id": request_id,
                    "method": request.method,
                    "route": route,
                    "status": status_code,
                    "duration_ms": round(duration * 1000, 3),
                },
                separators=(",", ":"),
            )
        )


def metrics_response() -> Response:
    """Render the process registry in Prometheus' text exposition format."""
    return Response(content=generate_latest(REGISTRY), media_type=CONTENT_TYPE_LATEST)
