from app.recommender import Recommendation
from app.reranking import diversify_by_category, diversity_candidate_limit


def test_category_diversity_covers_categories_before_filling_by_relevance() -> None:
    ranked = [
        Recommendation("sports-1", 10.0),
        Recommendation("sports-2", 9.0),
        Recommendation("news-1", 8.0),
        Recommendation("music-1", 7.0),
        Recommendation("sports-3", 6.0),
    ]
    categories = {
        "sports-1": "sports",
        "sports-2": "sports",
        "news-1": "news",
        "music-1": "music",
        "sports-3": "sports",
    }

    reranked = diversify_by_category(ranked, categories, limit=4)

    assert [item.item_id for item in reranked] == [
        "sports-1",
        "news-1",
        "music-1",
        "sports-2",
    ]
    assert [item.score for item in reranked] == [10.0, 8.0, 7.0, 9.0]


def test_category_diversity_preserves_order_when_metadata_is_unavailable() -> None:
    ranked = [Recommendation("a", 3.0), Recommendation("b", 2.0)]

    assert diversify_by_category(ranked, {}, limit=2) == ranked


def test_diversity_candidate_pool_is_bounded() -> None:
    assert diversity_candidate_limit(10) == 50
    assert diversity_candidate_limit(100) == 500
