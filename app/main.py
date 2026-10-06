from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated, Literal
from uuid import UUID, uuid4

from fastapi import FastAPI, Header, HTTPException, Path, Query, Response, status
from pydantic import BaseModel, Field, field_validator
from redis.exceptions import RedisError
from sqlalchemy.exc import SQLAlchemyError

from app.cache import RecommendationCache, build_recommendation_cache
from app.catalog import Item, ItemCatalog, build_item_catalog
from app.config import settings
from app.impressions import (
    ImpressionRepository,
    RecommendationImpression,
    build_impression_repository,
)
from app.observability import (
    CACHE_OPERATIONS,
    IMPRESSIONS_RECORDED,
    INTERACTION_INGESTIONS,
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
    RecommendationReason,
)
from app.repository import (
    BatchIdempotencyConflictError,
    IdempotencyConflictError,
    InteractionRepository,
    build_interaction_repository,
)
from app.reranking import (
    CATEGORY_DIVERSITY_VERSION,
    diversify_by_category,
    diversity_candidate_limit,
)


class InteractionRequest(BaseModel):
    user_id: str = Field(min_length=1, max_length=128)
    item_id: str = Field(min_length=1, max_length=128)
    interaction_type: InteractionType
    occurred_at: datetime | None = None
    recommendation_request_id: UUID | None = None

    @field_validator("occurred_at")
    @classmethod
    def validate_occurred_at(cls, value: datetime | None) -> datetime | None:
        if value is None:
            return None
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("occurred_at must include a timezone offset")
        return value.astimezone(UTC)


class InteractionResponse(BaseModel):
    user_id: str
    item_id: str
    interaction_type: InteractionType
    occurred_at: datetime | None
    idempotency_key: str | None
    recommendation_request_id: str | None
    status: str


class InteractionBatchRequest(BaseModel):
    batch_id: str = Field(
        min_length=1,
        max_length=128,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$",
    )
    interactions: list[InteractionRequest] = Field(min_length=1, max_length=500)


class InteractionBatchResponse(BaseModel):
    batch_id: str
    event_count: int
    status: str


class RecommendationResponse(BaseModel):
    item_id: str
    title: str | None
    category: str | None
    score: float
    reason: RecommendationReason
    supporting_item_count: int


class ItemRequest(BaseModel):
    title: str = Field(min_length=1, max_length=256)
    category: str = Field(min_length=1, max_length=128)
    is_active: bool = True

    @field_validator("title", "category")
    @classmethod
    def strip_nonempty_text(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("value must contain readable text")
        return value


class ItemResponse(BaseModel):
    item_id: str
    title: str
    category: str
    is_active: bool


app = FastAPI(
    title="Recommendation Platform",
    version="0.1.0",
    description="Production-oriented recommendation and ranking API.",
)
app.add_middleware(HttpObservabilityMiddleware)

interaction_repository: InteractionRepository = build_interaction_repository(settings.database_url)
impression_repository: ImpressionRepository = build_impression_repository(settings.database_url)
item_catalog: ItemCatalog = build_item_catalog(settings.database_url)
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
    if not item_catalog.is_ready():
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Item catalog is unavailable.",
        )
    if not impression_repository.is_ready():
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Impression repository is unavailable.",
        )

    return {
        "status": "ready",
        "storage": interaction_repository.backend,
        "catalog": item_catalog.backend,
        "cache": recommendation_cache.backend,
        "impressions": impression_repository.backend,
    }


@app.put("/items/{item_id}", response_model=ItemResponse, tags=["catalog"])
async def upsert_item(
    payload: ItemRequest,
    item_id: Annotated[
        str,
        Path(
            min_length=1,
            max_length=128,
            pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$",
        ),
    ],
) -> ItemResponse:
    """Create or replace catalog metadata and recommendation eligibility."""
    item = Item(item_id, payload.title, payload.category, payload.is_active)
    try:
        item_catalog.upsert(item)
    except SQLAlchemyError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Item catalog is unavailable.",
        ) from exc
    try:
        recommendation_cache.invalidate()
        CACHE_OPERATIONS.labels("invalidate", "success").inc()
    except RedisError as exc:
        CACHE_OPERATIONS.labels("invalidate", "error").inc()
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Item saved, but recommendation cache invalidation failed.",
        ) from exc
    return ItemResponse(
        item_id=item.item_id,
        title=item.title,
        category=item.category,
        is_active=item.is_active,
    )


@app.get("/items/{item_id}", response_model=ItemResponse, tags=["catalog"])
async def get_item(
    item_id: Annotated[
        str,
        Path(
            min_length=1,
            max_length=128,
            pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$",
        ),
    ],
) -> ItemResponse:
    try:
        item = item_catalog.get(item_id)
    except SQLAlchemyError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Item catalog is unavailable.",
        ) from exc
    if item is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Item not found.")
    return ItemResponse(
        item_id=item.item_id,
        title=item.title,
        category=item.category,
        is_active=item.is_active,
    )


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
async def record_interaction(
    payload: InteractionRequest,
    response: Response,
    idempotency_key: Annotated[
        str | None,
        Header(
            alias="Idempotency-Key",
            min_length=1,
            max_length=128,
            pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$",
        ),
    ] = None,
) -> InteractionResponse:
    """Record an interaction once and safely replay client retries."""
    interaction = Interaction(
        user_id=payload.user_id,
        item_id=payload.item_id,
        interaction_type=payload.interaction_type,
        occurred_at=payload.occurred_at,
        recommendation_request_id=(
            str(payload.recommendation_request_id)
            if payload.recommendation_request_id is not None
            else None
        ),
    )
    if payload.recommendation_request_id is not None:
        try:
            was_exposed = impression_repository.contains(
                str(payload.recommendation_request_id), payload.user_id, payload.item_id
            )
        except SQLAlchemyError as exc:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Impression repository is unavailable.",
            ) from exc
        if not was_exposed:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail="recommendation_request_id does not expose this item to this user.",
            )
    try:
        inserted = interaction_repository.add(interaction, idempotency_key)
    except IdempotencyConflictError as exc:
        INTERACTION_INGESTIONS.labels("conflict").inc()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Idempotency-Key was already used with a different interaction.",
        ) from exc
    except SQLAlchemyError as exc:
        INTERACTION_INGESTIONS.labels("storage_error").inc()
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Interaction repository is unavailable.",
        ) from exc

    outcome = "recorded" if inserted else "replayed"
    INTERACTION_INGESTIONS.labels(outcome).inc()
    if not inserted:
        response.status_code = status.HTTP_200_OK

    # Retry invalidation even for a replay. This repairs the case where storage
    # committed but the first request failed while invalidating the cache.
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
        occurred_at=interaction.occurred_at,
        idempotency_key=idempotency_key,
        recommendation_request_id=interaction.recommendation_request_id,
        status=outcome,
    )


@app.post(
    "/interactions/batch",
    response_model=InteractionBatchResponse,
    status_code=status.HTTP_201_CREATED,
    tags=["interactions"],
)
async def record_interaction_batch(
    payload: InteractionBatchRequest,
    response: Response,
) -> InteractionBatchResponse:
    """Atomically record a bounded, retry-safe ordered interaction batch."""
    interactions = tuple(
        Interaction(
            user_id=item.user_id,
            item_id=item.item_id,
            interaction_type=item.interaction_type,
            occurred_at=item.occurred_at,
            recommendation_request_id=(
                str(item.recommendation_request_id)
                if item.recommendation_request_id is not None
                else None
            ),
        )
        for item in payload.interactions
    )
    for interaction in interactions:
        if interaction.recommendation_request_id is None:
            continue
        try:
            was_exposed = impression_repository.contains(
                interaction.recommendation_request_id,
                interaction.user_id,
                interaction.item_id,
            )
        except SQLAlchemyError as exc:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Impression repository is unavailable.",
            ) from exc
        if not was_exposed:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail=(
                    "A recommendation_request_id does not expose its item "
                    "to its user."
                ),
            )

    try:
        inserted = interaction_repository.add_batch(interactions, payload.batch_id)
    except BatchIdempotencyConflictError as exc:
        INTERACTION_INGESTIONS.labels("batch_conflict").inc()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="batch_id was already used with different interactions.",
        ) from exc
    except SQLAlchemyError as exc:
        INTERACTION_INGESTIONS.labels("batch_storage_error").inc()
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Interaction repository is unavailable.",
        ) from exc

    outcome = "recorded" if inserted else "replayed"
    INTERACTION_INGESTIONS.labels(f"batch_{outcome}").inc()
    if not inserted:
        response.status_code = status.HTTP_200_OK

    # Replays repeat invalidation to repair a prior post-commit cache failure.
    try:
        recommendation_cache.invalidate()
        CACHE_OPERATIONS.labels("invalidate", "success").inc()
    except RedisError as exc:
        CACHE_OPERATIONS.labels("invalidate", "error").inc()
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Interaction batch recorded, but cache invalidation failed.",
        ) from exc

    return InteractionBatchResponse(
        batch_id=payload.batch_id,
        event_count=len(interactions),
        status=outcome,
    )


@app.get(
    "/recommendations/{user_id}",
    response_model=list[RecommendationResponse],
    tags=["recommendations"],
)
async def get_recommendations(
    response: Response,
    user_id: str,
    limit: int = Query(default=10, ge=1, le=100),
    strategy: Literal["personalized", "popular"] = Query(default="personalized"),
    diversity: Literal["none", "category"] = Query(default="none"),
) -> list[RecommendationResponse]:
    """Use versioned cache-aside ranking with optional category coverage."""
    try:
        catalog_items = item_catalog.list_all()
    except SQLAlchemyError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Item catalog is unavailable.",
        ) from exc
    items_by_id = {item.item_id: item for item in catalog_items}
    eligible_item_ids = (
        {item.item_id for item in catalog_items if item.is_active}
        if catalog_items
        else None
    )
    recommender_type = (
        PersonalizedRecommender if strategy == "personalized" else PopularityRecommender
    )
    model_version = recommender_type.model_version
    if diversity == "category":
        model_version = f"{model_version}-{CATEGORY_DIVERSITY_VERSION}"
    try:
        cache_lookup = recommendation_cache.lookup(user_id, strategy, model_version, limit)
    except RedisError:
        CACHE_OPERATIONS.labels("get", "error").inc()
        cached = None
        cache_generation = None
    else:
        cached = cache_lookup.recommendations
        cache_generation = cache_lookup.generation

    if cached is None:
        CACHE_OPERATIONS.labels("get", "miss").inc()

    if cached is not None:
        CACHE_OPERATIONS.labels("get", "hit").inc()
        RANKING_REQUESTS.labels(strategy, model_version, "cache").inc()
        RANKING_RESULTS.labels(strategy, "cache").inc(len(cached))
        response_payload = [
            RecommendationResponse(
                item_id=item.item_id,
                title=(items_by_id[item.item_id].title if item.item_id in items_by_id else None),
                category=(
                    items_by_id[item.item_id].category if item.item_id in items_by_id else None
                ),
                score=item.score,
                reason=item.reason,
                supporting_item_count=item.supporting_item_count,
            )
            for item in cached
        ]
        _record_impressions(
            response, user_id, strategy, model_version, "cache", response_payload
        )
        return response_payload

    interactions = interaction_repository.list_all()
    if not interactions:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No interactions are available yet.",
        )

    recommender = recommender_type(interactions, eligible_item_ids)
    candidate_limit = diversity_candidate_limit(limit) if diversity == "category" else limit
    recommendations = recommender.recommend(user_id=user_id, limit=candidate_limit)
    if diversity == "category":
        recommendations = diversify_by_category(
            recommendations,
            {item.item_id: item.category for item in catalog_items},
            limit,
        )

    if cache_generation is not None:
        try:
            stored = recommendation_cache.set_if_current(
                user_id,
                strategy,
                model_version,
                limit,
                recommendations,
                cache_generation,
            )
            CACHE_OPERATIONS.labels(
                "set", "success" if stored else "stale_generation"
            ).inc()
        except RedisError:
            CACHE_OPERATIONS.labels("set", "error").inc()
            # Cache is an optimization; ranking is still possible from source data.

    RANKING_REQUESTS.labels(strategy, model_version, "live").inc()
    RANKING_RESULTS.labels(strategy, "live").inc(len(recommendations))

    response_payload = [
        RecommendationResponse(
            item_id=item.item_id,
            title=(items_by_id[item.item_id].title if item.item_id in items_by_id else None),
            category=(
                items_by_id[item.item_id].category if item.item_id in items_by_id else None
            ),
            score=item.score,
            reason=item.reason,
            supporting_item_count=item.supporting_item_count,
        )
        for item in recommendations
    ]
    _record_impressions(response, user_id, strategy, model_version, "live", response_payload)
    return response_payload


def _record_impressions(
    response: Response,
    user_id: str,
    strategy: str,
    model_version: str,
    source: str,
    recommendations: list[RecommendationResponse],
) -> None:
    request_id = str(uuid4())
    served_at = datetime.now(UTC)
    impressions = tuple(
        RecommendationImpression(
            request_id=request_id,
            user_id=user_id,
            item_id=item.item_id,
            rank=rank,
            strategy=strategy,
            model_version=model_version,
            source=source,
            served_at=served_at,
        )
        for rank, item in enumerate(recommendations, start=1)
    )
    try:
        impression_repository.record_batch(impressions)
    except SQLAlchemyError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Recommendations could not be durably logged.",
        ) from exc
    response.headers["X-Recommendation-Request-ID"] = request_id
    response.headers["X-Recommendation-Model-Version"] = model_version
    IMPRESSIONS_RECORDED.labels(strategy, source).inc(len(impressions))
