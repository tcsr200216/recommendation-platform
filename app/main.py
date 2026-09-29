from __future__ import annotations

from fastapi import FastAPI, HTTPException, Query, status
from pydantic import BaseModel, Field

from app.recommender import Interaction, InteractionType, PopularityRecommender


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

# MVP persistence boundary. PostgreSQL-backed repositories will replace this
# in-memory collection without changing the API contract.
_interactions: list[Interaction] = []


@app.get("/health", tags=["system"])
async def health() -> dict[str, str]:
    """Return a lightweight liveness response for local and container health checks."""
    return {"status": "ok"}


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
    """Record a user-item interaction for recommendation generation."""
    interaction = Interaction(
        user_id=payload.user_id,
        item_id=payload.item_id,
        interaction_type=payload.interaction_type,
    )
    _interactions.append(interaction)

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
) -> list[RecommendationResponse]:
    """Return weighted-popularity recommendations excluding items the user already saw."""
    if not _interactions:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No interactions are available yet.",
        )

    recommender = PopularityRecommender(_interactions)
    recommendations = recommender.recommend(user_id=user_id, limit=limit)

    return [
        RecommendationResponse(item_id=item.item_id, score=item.score)
        for item in recommendations
    ]
