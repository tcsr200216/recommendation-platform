"""Deterministic offline ranking evaluation; never reads or changes live storage."""
from __future__ import annotations

import argparse
import json
from collections import defaultdict
from collections.abc import Iterable
from dataclasses import asdict, dataclass
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


def leave_one_out(
    interactions: Iterable[Interaction],
) -> tuple[tuple[Interaction, ...], dict[str, str], int]:
    """Hold out the lexicographically last distinct item per eligible user.

    All events for each held-out user/item pair are removed together. Users with
    fewer than two distinct items remain in training, but are not scored. There
    are no timestamps in the domain model, so this is not a temporal split.
    """
    events = tuple(interactions)
    items_by_user: dict[str, set[str]] = defaultdict(set)
    for event in events:
        items_by_user[event.user_id].add(event.item_id)
    targets = {
        user: max(items)
        for user, items in sorted(items_by_user.items())
        if len(items) >= 2
    }
    training = tuple(e for e in events if targets.get(e.user_id) != e.item_id)
    return training, targets, len(items_by_user) - len(targets)


def evaluate(
    interactions: Iterable[Interaction], k: int = 10, strategy: str = "personalized",
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


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("dataset", type=Path, help="JSON array of interaction objects")
    parser.add_argument("--k", type=int, default=10)
    parser.add_argument("--strategy", choices=["personalized", "popular", "both"], default="both")
    args = parser.parse_args()
    try:
        rows = json.loads(args.dataset.read_text())
        events = [
            Interaction(row["user_id"], row["item_id"], InteractionType(row["interaction_type"]))
            for row in rows
        ]
        if any(not isinstance(e.user_id, str) or not e.user_id.strip()
               or not isinstance(e.item_id, str) or not e.item_id.strip() for e in events):
            raise ValueError("user_id and item_id must be nonempty strings")
        strategies = ["popular", "personalized"] if args.strategy == "both" else [args.strategy]
        reports = [asdict(evaluate(events, args.k, s)) for s in strategies]
    except (OSError, ValueError, TypeError, KeyError) as exc:
        parser.error(str(exc))
    print(json.dumps(reports, indent=2, allow_nan=False))


if __name__ == "__main__":
    main()
