"""Deterministic offline ranking evaluation; never reads or changes live storage."""

from __future__ import annotations

import argparse
import hashlib
import json
from collections import Counter, defaultdict
from collections.abc import Iterable
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path

from app.recommender import (
    Interaction,
    InteractionType,
    PersonalizedRecommender,
    PopularityRecommender,
)


@dataclass(frozen=True)
class EvaluationReport:
    strategy: str
    model_version: str
    k: int
    total_users: int
    evaluated_users: int
    skipped_sparse_users: int
    cold_target_users: int
    hit_rate_at_k: float | None
    mrr_at_k: float | None


REPORT_SCHEMA_VERSION = "2"
SPLIT_VERSION = "leave-latest-item-out-v1"


def _normalized_timestamp(value: datetime | None) -> str | None:
    if value is None:
        return None
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("Interaction timestamps must include a timezone offset")
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


def _parse_timestamp(value: object) -> datetime | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise TypeError("occurred_at must be an ISO-8601 string or null")
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as exc:
        raise ValueError("occurred_at must be a valid ISO-8601 timestamp") from exc
    _normalized_timestamp(parsed)
    return parsed.astimezone(UTC)


def dataset_version(interactions: Iterable[Interaction]) -> str:
    """Return an order-independent fingerprint that preserves duplicate counts."""
    counts = Counter(
        (
            event.user_id,
            event.item_id,
            event.interaction_type.value,
            _normalized_timestamp(event.occurred_at),
        )
        for event in interactions
    )
    canonical_rows = [
        {
            "user_id": user_id,
            "item_id": item_id,
            "interaction_type": interaction_type,
            "occurred_at": occurred_at,
            "count": count,
        }
        for (user_id, item_id, interaction_type, occurred_at), count in sorted(
            counts.items(),
            key=lambda entry: tuple("" if value is None else value for value in entry[0]),
        )
    ]
    canonical = json.dumps(canonical_rows, sort_keys=True, separators=(",", ":"))
    return f"sha256:{hashlib.sha256(canonical.encode()).hexdigest()}"


def leave_one_out(
    interactions: Iterable[Interaction],
) -> tuple[tuple[Interaction, ...], dict[str, str], int]:
    """Hold out each eligible user's latest distinct item by event time.

    All events for each held-out user/item pair are removed together. Users with
    fewer than two distinct items or missing event timestamps remain in training,
    but are not scored. Item ID deterministically breaks equal-timestamp ties.
    """
    events = tuple(interactions)
    events_by_user: dict[str, list[Interaction]] = defaultdict(list)
    for event in events:
        events_by_user[event.user_id].append(event)
    targets: dict[str, str] = {}
    for user, user_events in sorted(events_by_user.items()):
        if len({event.item_id for event in user_events}) < 2:
            continue
        if any(event.occurred_at is None for event in user_events):
            continue
        latest = max(
            user_events,
            key=lambda event: (_normalized_timestamp(event.occurred_at), event.item_id),
        )
        targets[user] = latest.item_id
    training = tuple(e for e in events if targets.get(e.user_id) != e.item_id)
    return training, targets, len(events_by_user) - len(targets)


def evaluate(
    interactions: Iterable[Interaction],
    k: int = 10,
    strategy: str = "personalized",
) -> EvaluationReport:
    if isinstance(k, bool) or not isinstance(k, int) or k < 1:
        raise ValueError("k must be a positive integer")
    models = {"personalized": PersonalizedRecommender, "popular": PopularityRecommender}
    if strategy not in models:
        raise ValueError("strategy must be personalized or popular")
    training, targets, skipped = leave_one_out(interactions)
    model = models[strategy](training)
    catalog = {e.item_id for e in training}
    hits = 0
    reciprocal_ranks = []
    for user, target in targets.items():
        ranked = [r.item_id for r in model.recommend(user, limit=k)]
        if target in ranked:
            hits += 1
            reciprocal_ranks.append(1.0 / (ranked.index(target) + 1))
        else:
            reciprocal_ranks.append(0.0)
    count = len(targets)
    return EvaluationReport(
        strategy=strategy,
        model_version=model.model_version,
        k=k,
        total_users=count + skipped,
        evaluated_users=count,
        skipped_sparse_users=skipped,
        cold_target_users=sum(target not in catalog for target in targets.values()),
        hit_rate_at_k=hits / count if count else None,
        mrr_at_k=sum(reciprocal_ranks) / count if count else None,
    )


def build_report(
    interactions: Iterable[Interaction],
    k: int = 10,
    strategy: str = "both",
) -> dict[str, object]:
    """Build a stable, machine-readable evaluation artifact."""
    events = tuple(interactions)
    if strategy not in {"personalized", "popular", "both"}:
        raise ValueError("strategy must be personalized, popular, or both")
    strategies = ["popular", "personalized"] if strategy == "both" else [strategy]
    return {
        "report_schema_version": REPORT_SCHEMA_VERSION,
        "dataset": {
            "version": dataset_version(events),
            "interaction_count": len(events),
        },
        "evaluation": {
            "split_version": SPLIT_VERSION,
            "k": k,
        },
        "results": [asdict(evaluate(events, k, selected)) for selected in strategies],
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("dataset", type=Path, help="JSON array of interaction objects")
    parser.add_argument("--k", type=int, default=10)
    parser.add_argument("--strategy", choices=["personalized", "popular", "both"], default="both")
    parser.add_argument("--output", type=Path, help="also write the JSON report to this path")
    args = parser.parse_args()
    try:
        rows = json.loads(args.dataset.read_text())
        events = [
            Interaction(
                row["user_id"],
                row["item_id"],
                InteractionType(row["interaction_type"]),
                _parse_timestamp(row.get("occurred_at")),
            )
            for row in rows
        ]
        if any(
            not isinstance(e.user_id, str)
            or not e.user_id.strip()
            or not isinstance(e.item_id, str)
            or not e.item_id.strip()
            for e in events
        ):
            raise ValueError("user_id and item_id must be nonempty strings")
        report = build_report(events, args.k, args.strategy)
        rendered = json.dumps(report, indent=2, allow_nan=False) + "\n"
        if args.output:
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_text(rendered)
    except (OSError, ValueError, TypeError, KeyError) as exc:
        parser.error(str(exc))
    print(rendered, end="")


if __name__ == "__main__":
    main()
