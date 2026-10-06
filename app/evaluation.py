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

from app.catalog import Item
from app.recommender import (
    Interaction,
    InteractionType,
    PersonalizedRecommender,
    PopularityRecommender,
)
from app.reranking import (
    CATEGORY_DIVERSITY_VERSION,
    diversify_by_category,
    diversity_candidate_limit,
)


@dataclass(frozen=True)
class EvaluationReport:
    strategy: str
    diversity: str
    model_version: str
    k: int
    total_users: int
    evaluated_users: int
    skipped_sparse_users: int
    cold_target_users: int
    ineligible_target_users: int
    hit_rate_at_k: float | None
    mrr_at_k: float | None
    average_unique_categories_at_k: float | None
    category_coverage_at_k: float | None


REPORT_SCHEMA_VERSION = "3"
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


def catalog_version(items: Iterable[Item]) -> str:
    """Fingerprint ranking-relevant catalog fields independent of input order."""
    canonical_rows = [
        {
            "category": item.category,
            "is_active": item.is_active,
            "item_id": item.item_id,
        }
        for item in sorted(items, key=lambda item: item.item_id)
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
    items: Iterable[Item] | None = None,
    diversity: str = "none",
) -> EvaluationReport:
    if isinstance(k, bool) or not isinstance(k, int) or k < 1:
        raise ValueError("k must be a positive integer")
    models = {"personalized": PersonalizedRecommender, "popular": PopularityRecommender}
    if strategy not in models:
        raise ValueError("strategy must be personalized or popular")
    if diversity not in {"none", "category"}:
        raise ValueError("diversity must be none or category")
    catalog_supplied = items is not None
    catalog_items = tuple(items or ())
    if diversity == "category" and not catalog_supplied:
        raise ValueError("category diversity evaluation requires an item catalog")
    active_items = {item.item_id: item for item in catalog_items if item.is_active}
    eligible_item_ids = set(active_items) if catalog_supplied else None
    categories = {item_id: item.category for item_id, item in active_items.items()}
    training, targets, skipped = leave_one_out(interactions)
    model = models[strategy](training, eligible_item_ids)
    training_catalog = {e.item_id for e in training}
    hits = 0
    reciprocal_ranks: list[float] = []
    unique_category_counts: list[int] = []
    for user, target in targets.items():
        candidate_limit = diversity_candidate_limit(k) if diversity == "category" else k
        recommendations = model.recommend(user, limit=candidate_limit)
        if diversity == "category":
            recommendations = diversify_by_category(
                recommendations,
                categories,
                k,
            )
        ranked = [recommendation.item_id for recommendation in recommendations[:k]]
        if target in ranked:
            hits += 1
            reciprocal_ranks.append(1.0 / (ranked.index(target) + 1))
        else:
            reciprocal_ranks.append(0.0)
        if catalog_supplied:
            unique_category_counts.append(
                len({active_items[item_id].category for item_id in ranked if item_id in active_items})
            )
    count = len(targets)
    available_category_count = len({item.category for item in active_items.values()})
    coverage_denominator = min(k, available_category_count)
    model_version = model.model_version
    if diversity == "category":
        model_version = f"{model_version}-{CATEGORY_DIVERSITY_VERSION}"
    return EvaluationReport(
        strategy=strategy,
        diversity=diversity,
        model_version=model_version,
        k=k,
        total_users=count + skipped,
        evaluated_users=count,
        skipped_sparse_users=skipped,
        cold_target_users=sum(target not in training_catalog for target in targets.values()),
        ineligible_target_users=(
            sum(target not in active_items for target in targets.values())
            if catalog_supplied
            else 0
        ),
        hit_rate_at_k=hits / count if count else None,
        mrr_at_k=sum(reciprocal_ranks) / count if count else None,
        average_unique_categories_at_k=(
            sum(unique_category_counts) / count if count and catalog_supplied else None
        ),
        category_coverage_at_k=(
            sum(value / coverage_denominator for value in unique_category_counts) / count
            if count and coverage_denominator
            else None
        ),
    )


def build_report(
    interactions: Iterable[Interaction],
    k: int = 10,
    strategy: str = "both",
    items: Iterable[Item] | None = None,
    diversity: str = "none",
) -> dict[str, object]:
    """Build a stable, machine-readable evaluation artifact."""
    events = tuple(interactions)
    if strategy not in {"personalized", "popular", "both"}:
        raise ValueError("strategy must be personalized, popular, or both")
    if diversity not in {"none", "category", "both"}:
        raise ValueError("diversity must be none, category, or both")
    catalog_supplied = items is not None
    catalog_items = tuple(items or ())
    if diversity in {"category", "both"} and not catalog_supplied:
        raise ValueError("category diversity evaluation requires an item catalog")
    strategies = ["popular", "personalized"] if strategy == "both" else [strategy]
    diversity_policies = ["none", "category"] if diversity == "both" else [diversity]
    datasets: dict[str, object] = {
        "interactions": {
            "version": dataset_version(events),
            "interaction_count": len(events),
        }
    }
    if catalog_supplied:
        datasets["catalog"] = {
            "version": catalog_version(catalog_items),
            "item_count": len(catalog_items),
            "active_item_count": sum(item.is_active for item in catalog_items),
        }
    return {
        "report_schema_version": REPORT_SCHEMA_VERSION,
        "datasets": datasets,
        "evaluation": {
            "split_version": SPLIT_VERSION,
            "k": k,
        },
        "results": [
            asdict(evaluate(events, k, selected, catalog_items if catalog_supplied else None, policy))
            for selected in strategies
            for policy in diversity_policies
        ],
    }


def _load_catalog(path: Path) -> tuple[Item, ...]:
    rows = json.loads(path.read_text())
    if not isinstance(rows, list):
        raise TypeError("catalog must be a JSON array")
    items = tuple(
        Item(
            item_id=row["item_id"],
            title=row["title"],
            category=row["category"],
            is_active=row.get("is_active", True),
        )
        for row in rows
    )
    if any(
        not isinstance(item.item_id, str)
        or not item.item_id.strip()
        or not isinstance(item.title, str)
        or not item.title.strip()
        or not isinstance(item.category, str)
        or not item.category.strip()
        or not isinstance(item.is_active, bool)
        for item in items
    ):
        raise ValueError("catalog fields must contain valid item_id, title, category, and is_active")
    if len({item.item_id for item in items}) != len(items):
        raise ValueError("catalog item_id values must be unique")
    return items


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("dataset", type=Path, help="JSON array of interaction objects")
    parser.add_argument("--k", type=int, default=10)
    parser.add_argument("--strategy", choices=["personalized", "popular", "both"], default="both")
    parser.add_argument("--catalog", type=Path, help="JSON array of item catalog objects")
    parser.add_argument("--diversity", choices=["none", "category", "both"], default="none")
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
        items = _load_catalog(args.catalog) if args.catalog is not None else None
        report = build_report(events, args.k, args.strategy, items, args.diversity)
        rendered = json.dumps(report, indent=2, allow_nan=False) + "\n"
        if args.output:
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_text(rendered)
    except (OSError, ValueError, TypeError, KeyError) as exc:
        parser.error(str(exc))
    print(rendered, end="")


if __name__ == "__main__":
    main()
