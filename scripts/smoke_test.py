"""Exercise the deployed REST path using only the Python standard library."""

from __future__ import annotations

import argparse
import json
import time
import urllib.error
import urllib.request
import uuid


def request(base_url: str, path: str, *, payload: dict[str, str] | None = None):
    body = None if payload is None else json.dumps(payload).encode("utf-8")
    method = "GET" if payload is None else "POST"
    req = urllib.request.Request(
        f"{base_url.rstrip('/')}{path}",
        data=body,
        method=method,
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=5) as response:
        return response.status, json.load(response)


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

    events = [
        {"user_id": target, "item_id": shared, "interaction_type": "like"},
        {"user_id": neighbor, "item_id": shared, "interaction_type": "like"},
        {"user_id": neighbor, "item_id": candidate, "interaction_type": "purchase"},
    ]
    for event in events:
        status, payload = request(args.base_url, "/interactions", payload=event)
        if status != 201 or payload.get("status") != "recorded":
            raise RuntimeError(f"interaction write failed: status={status}, payload={payload}")

    status, recommendations = request(
        args.base_url,
        f"/recommendations/{target}?strategy=personalized&limit=10",
    )
    if status != 200 or not recommendations or recommendations[0]["item_id"] != candidate:
        raise RuntimeError(
            f"unexpected recommendations: status={status}, payload={recommendations}"
        )
    if recommendations[0]["reason"] != "similar_users":
        raise RuntimeError(f"unexpected explanation: {recommendations[0]}")
    if recommendations[0]["supporting_item_count"] != 1:
        raise RuntimeError(f"unexpected supporting evidence: {recommendations[0]}")
    print("Smoke test passed: readiness, interaction writes, cache invalidation, and ranking work.")


if __name__ == "__main__":
    main()
