"""Exercise the deployed REST path using only the Python standard library."""

from __future__ import annotations

import argparse
import json
import time
import urllib.error
import urllib.request
import uuid


def request(
    base_url: str,
    path: str,
    *,
    payload: dict[str, str | bool] | None = None,
    method: str | None = None,
):
    body = None if payload is None else json.dumps(payload).encode("utf-8")
    method = method or ("GET" if payload is None else "POST")
    req = urllib.request.Request(
        f"{base_url.rstrip('/')}{path}",
        data=body,
        method=method,
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=5) as response:
        return response.status, json.load(response)


def request_recommendations(base_url: str, path: str) -> tuple[int, list[dict], str]:
    req = urllib.request.Request(f"{base_url.rstrip('/')}{path}")
    with urllib.request.urlopen(req, timeout=5) as response:
        request_id = response.headers.get("X-Recommendation-Request-ID")
        if request_id is None:
            raise RuntimeError("recommendation response omitted its attribution request ID")
        return response.status, json.load(response), request_id


def wait_until_ready(base_url: str, timeout_seconds: int) -> None:
    deadline = time.monotonic() + timeout_seconds
    last_error: Exception | None = None
    while time.monotonic() < deadline:
        try:
            status, payload = request(base_url, "/ready")
            if status == 200 and payload.get("status") == "ready":
                return
        except (OSError, ValueError, urllib.error.HTTPError) as exc:
            last_error = exc
        time.sleep(1)
    raise RuntimeError(f"service was not ready within {timeout_seconds}s: {last_error}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    parser.add_argument("--timeout", type=int, default=90)
    args = parser.parse_args()

    wait_until_ready(args.base_url, args.timeout)
    run_id = uuid.uuid4().hex
    target = f"smoke-target-{run_id}"
    neighbor = f"smoke-neighbor-{run_id}"
    shared = f"smoke-shared-{run_id}"
    candidate = f"smoke-candidate-{run_id}"
    retired = f"smoke-retired-{run_id}"

    catalog = {
        shared: {"title": "Smoke Shared Signal", "category": "verification", "is_active": True},
        candidate: {
            "title": "Smoke Active Candidate",
            "category": "verification",
            "is_active": True,
        },
        retired: {
            "title": "Smoke Retired Candidate",
            "category": "verification",
            "is_active": False,
        },
    }
    for item_id, item in catalog.items():
        status, saved = request(args.base_url, f"/items/{item_id}", payload=item, method="PUT")
        if status != 200 or saved.get("item_id") != item_id:
            raise RuntimeError(f"catalog write failed: status={status}, payload={saved}")
    status, saved = request(args.base_url, f"/items/{candidate}")
    if status != 200 or saved.get("title") != "Smoke Active Candidate":
        raise RuntimeError(f"catalog read failed: status={status}, payload={saved}")

    events = [
        {
            "user_id": target,
            "item_id": shared,
            "interaction_type": "like",
            "occurred_at": "2026-10-04T18:00:00Z",
        },
        {
            "user_id": neighbor,
            "item_id": shared,
            "interaction_type": "like",
            "occurred_at": "2026-10-04T18:01:00Z",
        },
        {
            "user_id": neighbor,
            "item_id": candidate,
            "interaction_type": "click",
            "occurred_at": "2026-10-04T18:02:00Z",
        },
        {
            "user_id": neighbor,
            "item_id": retired,
            "interaction_type": "purchase",
            "occurred_at": "2026-10-04T18:03:00Z",
        },
    ]
    for event in events:
        status, payload = request(args.base_url, "/interactions", payload=event)
        if status != 201 or payload.get("status") != "recorded":
            raise RuntimeError(f"interaction write failed: status={status}, payload={payload}")
        if not payload.get("occurred_at", "").endswith("Z"):
            raise RuntimeError(f"interaction timestamp was not normalized: {payload}")

    status, recommendations, recommendation_request_id = request_recommendations(
        args.base_url,
        f"/recommendations/{target}?strategy=personalized&limit=10",
    )
    if status != 200 or not recommendations or recommendations[0]["item_id"] != candidate:
        raise RuntimeError(
            f"unexpected recommendations: status={status}, payload={recommendations}"
        )
    if recommendations[0]["reason"] != "similar_users":
        raise RuntimeError(f"unexpected explanation: {recommendations[0]}")
    if recommendations[0]["title"] != "Smoke Active Candidate":
        raise RuntimeError(f"catalog metadata was not enriched: {recommendations[0]}")
    if any(item["item_id"] == retired for item in recommendations):
        raise RuntimeError(f"inactive item was recommended: {recommendations}")
    if recommendations[0]["supporting_item_count"] != 1:
        raise RuntimeError(f"unexpected supporting evidence: {recommendations[0]}")
    status, feedback = request(
        args.base_url,
        "/interactions",
        payload={
            "user_id": target,
            "item_id": candidate,
            "interaction_type": "click",
            "recommendation_request_id": recommendation_request_id,
        },
    )
    if status != 201 or feedback.get("recommendation_request_id") != recommendation_request_id:
        raise RuntimeError(f"recommendation attribution failed: status={status}, payload={feedback}")
    print(
        "Smoke test passed: catalog eligibility, interaction writes, cache invalidation, "
        "metadata-enriched ranking, and durable impression attribution work."
    )


if __name__ == "__main__":
    main()
