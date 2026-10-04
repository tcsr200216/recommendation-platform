import pytest

from app.evaluation import build_report, dataset_version, evaluate, leave_one_out
from app.recommender import Interaction, InteractionType


def event(user, item):
    return Interaction(user, item, InteractionType.LIKE)


def test_split_removes_every_event_for_target_pair():
    events = [event("u", "a"), event("u", "z"), event("u", "z"), event("sparse", "z")]
    training, targets, skipped = leave_one_out(events)
    assert training == (events[0], events[3])
    assert targets == {"u": "z"}
    assert skipped == 1
    assert events == [event("u", "a"), event("u", "z"), event("u", "z"), event("sparse", "z")]


def test_known_rank_two_metrics_include_sparse_users_only_as_training():
    events = [event("target", "a"), event("target", "z"),
              event("one", "b"), event("two", "b"), event("three", "z")]
    report = evaluate(events, k=2, strategy="popular")
    assert report.hit_rate_at_k == 1.0
    assert report.mrr_at_k == 0.5
    assert report.evaluated_users == 1
    assert report.skipped_sparse_users == 3
    assert report.total_users == 4
    assert report.model_version == "weighted-popularity-v2"
    assert evaluate(events, k=1, strategy="popular").hit_rate_at_k == 0.0


def test_personalized_uses_neighbor_signal():
    events = [event("target", "a"), event("target", "z"), event("neighbor", "a"),
              event("neighbor", "z"), event("neighbor", "zz")]
    report = evaluate(events, k=1)
    assert report.hit_rate_at_k == 0.5
    assert report.mrr_at_k == 0.5
    assert report.cold_target_users == 1
    assert report.model_version == "user-cosine-v2"


def test_unknown_targets_are_misses_and_not_dropped():
    report = evaluate([event("u", "a"), event("u", "z")])
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
    events = [event("u", "a"), event("u", "z"), event("n", "a"), event("n", "z")]
    assert evaluate(events) == evaluate(reversed(events))


def test_dataset_version_is_order_independent_but_preserves_duplicate_counts():
    events = [event("u", "a"), event("u", "z"), event("u", "a")]
    version = dataset_version(events)
    assert version.startswith("sha256:")
    assert len(version) == len("sha256:") + 64
    assert version == dataset_version(reversed(events))
    assert version != dataset_version(events[:-1])


def test_build_report_carries_reproducibility_metadata_and_model_versions():
    events = [event("u", "a"), event("u", "z"), event("n", "a"), event("n", "z")]
    report = build_report(events, k=2)
    assert report["report_schema_version"] == "1"
    assert report["dataset"] == {
        "version": dataset_version(events),
        "interaction_count": 4,
    }
    assert report["evaluation"] == {
        "split_version": "leave-one-out-lexicographic-v1",
        "k": 2,
    }
    assert [result["model_version"] for result in report["results"]] == [
        "weighted-popularity-v2",
        "user-cosine-v2",
    ]


@pytest.mark.parametrize("k", [0, -1, True, 1.5])
def test_invalid_k(k):
    with pytest.raises(ValueError, match="positive integer"):
        evaluate([], k=k)


def test_invalid_strategy():
    with pytest.raises(ValueError, match="strategy"):
        evaluate([], strategy="unknown")
