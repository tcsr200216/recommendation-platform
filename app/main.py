from __future__ import annotations

from typing import Literal

from fastapi import FastAPI, HTTPException, Query, status
from pydantic import BaseModel, Field
from redis.exceptions import RedisError

from app.cache import RecommendationCache, build_recommendation_cache
from app.config import settings
from app.observability import (
    CACHE_OPERATIONS,
    RANKING_REQUESTS,
    RANKING_RESULTS,
    HttpObservabilityMiddleware,
    metrics_response,
)
from app.recommender import (
    Interaction,
    InteractionType,
    PersonalizedRecommender,
    PopularityRecommender,
)
from app.repository import InteractionRepository, build_interaction_repository


class InteractionRequest(BaseModel):
    user_id: str = Field(min_length=1, max_length=128)
    item_id: str = Field(min_length=1, max_length=128)
    interaction_type: InteractionType


class InteractionResponse(BaseModel):
    user_id: str
    item_id: str
    interaction_type: InteractionType
    status: str


class RecommendationResponse(BaseModel):
    item_id: str
    score: float


app = FastAPI(
    title="Recommendation Platform",
    version="0.1.0",
    description="Production-oriented recommendation and ranking API.",
)
app.add_middleware(HttpObservabilityMiddleware)

interaction_repository: InteractionRepository = build_interaction_repository(
    settings.database_url
)
recommendation_cache: RecommendationCache = build_recommendation_cache(
    settings.redis_url,
    ttl_seconds=settings.cache_ttl_seconds,
    namespace=settings.cache_namespace,
)


@app.get("/health", tags=["system"])
async def health() -> dict[str, str]:
    """Return process liveness without depending on external services."""
    return {"status": "ok"}


@app.get("/metrics", include_in_schema=False)
async def metrics():
    """Expose Prometheus metrics without adding the scrape itself to HTTP totals."""
    return metrics_response()


@app.get("/ready", tags=["system"])
async def ready() -> dict[str, str]:
    """Check the selected persistence and cache dependencies."""
    if not interaction_repository.is_ready():
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Interaction repository is unavailable.",
        )
    if not recommendation_cache.is_ready():
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Recommendation cache is unavailable.",
        )

    return {
        "status": "ready",
        "storage": interaction_repository.backend,
        "cache": recommendation_cache.backend,
    }


@app.get("/", tags=["system"])
async def root() -> dict[str, str]:
    return {
        "service": "recommendation-platform",
        "status": "running",
    }


@app.post(
    "/interactions",
    response_model=InteractionResponse,
    status_code=status.HTTP_201_CREATED,
    tags=["interactions"],
)
async def record_interaction(payload: InteractionRequest) -> InteractionResponse:
    """Record a user-item interaction and invalidate any cached rankings."""
    interaction = Interaction(
        user_id=payload.user_id,
        item_id=payload.item_id,
        interaction_type=payload.interaction_type,
    )
    interaction_repository.add(interaction)
    try:
        recommendation_cache.invalidate()
        CACHE_OPERATIONS.labels("invalidate", "success").inc()
    except RedisError as exc:
        CACHE_OPERATIONS.labels("invalidate", "error").inc()
        # Recording succeeded; do not imply that a retry would be harmless.
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Interaction recorded, but recommendation cache invalidation failed.",
        ) from exc

    return InteractionResponse(
        user_id=interaction.user_id,
        item_id=interaction.item_id,
        interaction_type=interaction.interaction_type,
        status="recorded",
    )


@app.get(
    "/recommendations/{user_id}",
    response_model=list[RecommendationResponse],
    tags=["recommendations"],
)
async def get_recommendations(
    user_id: str,
    limit: int = Query(default=10, ge=1, le=100),
    strategy: Literal["personalized", "popular"] = Query(default="personalized"),
) -> list[RecommendationResponse]:
    """Use versioned cache-aside ranking, with a live fallback on cache read failure."""
    recommender_type = (
        PersonalizedRecommender if strategy == "personalized" else PopularityRecommender
    )
    model_version = recommender_type.model_version
    try:
        cached = recommendation_cache.get(user_id, strategy, model_version, limit)
    except RedisError:
        CACHE_OPERATIONS.labels("get", "error").inc()
        cached = None

    if cached is None:
        CACHE_OPERATIONS.labels("get", "miss").inc()

    if cached is not None:
        CACHE_OPERATIONS.labels("get", "hit").inc()
        RANKING_REQUESTS.labels(strategy, model_version, "cache").inc()
        RANKING_RESULTS.labels(strategy, "cache").inc(len(cached))
        return [
            RecommendationResponse(item_id=item.item_id, score=item.score)
            for item in cached
        ]

    interactions = interaction_repository.list_all()
    if not interactions:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No interactions are available yet.",
        )

    recommender = recommender_type(interactions)
    recommendations = recommender.recommend(user_id=user_id, limit=limit)

    try:
        recommendation_cache.set(user_id, strategy, model_version, limit, recommendations)
        CACHE_OPERATIONS.labels("set", "success").inc()
    except RedisError:
        CACHE_OPERATIONS.labels("set", "error").inc()
        # Cache is an optimization; ranking is still possible from source data.

    RANKING_REQUESTS.labels(strategy, model_version, "live").inc()
    RANKING_RESULTS.labels(strategy, "live").inc(len(recommendations))

    return [
        RecommendationResponse(item_id=item.item_id, score=item.score)
        for item in recommendations
    ]
