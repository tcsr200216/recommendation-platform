from datetime import UTC, datetime

import pytest

from app.catalog import Item
from app.evaluation import (
    build_report,
    catalog_version,
    dataset_version,
    evaluate,
    leave_one_out,
)
from app.recommender import Interaction, InteractionType


def event(user, item, day=1):
    return Interaction(user, item, InteractionType.LIKE, datetime(2026, 9, day, tzinfo=UTC))


def test_split_removes_every_event_for_target_pair():
    events = [event("u", "a", 3), event("u", "z", 1), event("u", "z", 2), event("sparse", "z")]
    training, targets, skipped = leave_one_out(events)
    assert training == (events[1], events[2], events[3])
    assert targets == {"u": "a"}
    assert skipped == 1
    assert events == [
        event("u", "a", 3),
        event("u", "z", 1),
        event("u", "z", 2),
        event("sparse", "z"),
    ]


def test_known_rank_two_metrics_include_sparse_users_only_as_training():
    events = [
        event("target", "a", 1),
        event("target", "z", 2),
        event("one", "b"),
        event("two", "b"),
        event("three", "z"),
    ]
    report = evaluate(events, k=2, strategy="popular")
    assert report.hit_rate_at_k == 1.0
    assert report.mrr_at_k == 0.5
    assert report.evaluated_users == 1
    assert report.skipped_sparse_users == 3
    assert report.total_users == 4
    assert report.model_version == "catalog-aware-time-decayed-popularity-v4-30d"
    assert evaluate(events, k=1, strategy="popular").hit_rate_at_k == 0.0


def test_personalized_uses_neighbor_signal():
    events = [
        event("target", "a", 1),
        event("target", "z", 2),
        event("neighbor", "a", 1),
        event("neighbor", "z", 2),
        event("neighbor", "zz", 3),
    ]
    report = evaluate(events, k=1)
    assert report.hit_rate_at_k == 0.5
    assert report.mrr_at_k == 0.5
    assert report.cold_target_users == 1
    assert report.model_version == "catalog-aware-time-decayed-user-cosine-v4-30d"


def test_unknown_targets_are_misses_and_not_dropped():
    report = evaluate([event("u", "a", 1), event("u", "z", 2)])
    assert report.evaluated_users == 1
    assert report.cold_target_users == 1
    assert report.hit_rate_at_k == report.mrr_at_k == 0.0


@pytest.mark.parametrize("events", [[], [event("u", "a"), event("u", "a")]])
def test_no_eligible_users_returns_null_metrics(events):
    report = evaluate(events)
    assert report.evaluated_users == 0
    assert report.hit_rate_at_k is None
    assert report.mrr_at_k is None


def test_deterministic_report_under_input_reordering():
    events = [event("u", "a", 1), event("u", "z", 2), event("n", "a", 1), event("n", "z", 2)]
    assert evaluate(events) == evaluate(reversed(events))


def test_dataset_version_is_order_independent_but_preserves_duplicate_counts():
    events = [event("u", "a", 1), event("u", "z", 2), event("u", "a", 1)]
    version = dataset_version(events)
    assert version.startswith("sha256:")
    assert len(version) == len("sha256:") + 64
    assert version == dataset_version(reversed(events))
    assert version != dataset_version(events[:-1])
    assert version != dataset_version([event("u", "a", 2), *events[1:]])


def test_users_with_missing_timestamps_are_not_temporally_scored():
    events = [
        Interaction("legacy", "a", InteractionType.LIKE),
        event("legacy", "b", 2),
        event("eligible", "a", 1),
        event("eligible", "b", 2),
    ]

    training, targets, skipped = leave_one_out(events)

    assert targets == {"eligible": "b"}
    assert skipped == 1
    assert events[0] in training


def test_build_report_carries_reproducibility_metadata_and_model_versions():
    events = [event("u", "a", 1), event("u", "z", 2), event("n", "a", 1), event("n", "z", 2)]
    items = [Item("a", "A", "books"), Item("z", "Z", "videos")]
    report = build_report(events, k=2, items=items, diversity="both")
    assert report["report_schema_version"] == "3"
    assert report["datasets"] == {
        "interactions": {
            "version": dataset_version(events),
            "interaction_count": 4,
        },
        "catalog": {
            "version": catalog_version(items),
            "item_count": 2,
            "active_item_count": 2,
        },
    }
    assert report["evaluation"] == {
        "split_version": "leave-latest-item-out-v1",
        "k": 2,
    }
    assert [result["model_version"] for result in report["results"]] == [
        "catalog-aware-time-decayed-popularity-v4-30d",
        "catalog-aware-time-decayed-popularity-v4-30d-category-coverage-v1-pool5x",
        "catalog-aware-time-decayed-user-cosine-v4-30d",
        "catalog-aware-time-decayed-user-cosine-v4-30d-category-coverage-v1-pool5x",
    ]


def test_category_diversity_reports_relevance_and_coverage_tradeoff():
    events = [
        event("target", "seen", 1),
        event("target", "held-out", 2),
        *(event(f"book-b-{index}", "book-b") for index in range(3)),
        *(event(f"book-c-{index}", "book-c") for index in range(2)),
        event("video-user", "held-out"),
    ]
    items = [
        Item("seen", "Seen", "books"),
        Item("book-b", "Book B", "books"),
        Item("book-c", "Book C", "books"),
        Item("held-out", "Held Out", "videos"),
    ]

    relevance = evaluate(events, k=2, strategy="popular", items=items)
    diverse = evaluate(events, k=2, strategy="popular", items=items, diversity="category")

    assert relevance.category_coverage_at_k == 0.5
    assert relevance.average_unique_categories_at_k == 1.0
    assert relevance.hit_rate_at_k == 0.0
    assert diverse.category_coverage_at_k == 1.0
    assert diverse.average_unique_categories_at_k == 2.0
    assert diverse.hit_rate_at_k == 1.0


def test_catalog_version_tracks_ranking_fields_not_input_order():
    items = [Item("a", "First title", "books"), Item("b", "B", "videos", False)]

    assert catalog_version(items) == catalog_version(reversed(items))
    assert catalog_version(items) == catalog_version(
        [Item("a", "Renamed title", "books"), Item("b", "B", "videos", False)]
    )
    assert catalog_version(items) != catalog_version(
        [Item("a", "First title", "courses"), Item("b", "B", "videos", False)]
    )


@pytest.mark.parametrize("k", [0, -1, True, 1.5])
def test_invalid_k(k):
    with pytest.raises(ValueError, match="positive integer"):
        evaluate([], k=k)


def test_invalid_strategy():
    with pytest.raises(ValueError, match="strategy"):
        evaluate([], strategy="unknown")


def test_category_diversity_requires_catalog():
    with pytest.raises(ValueError, match="item catalog"):
        evaluate([], diversity="category")
