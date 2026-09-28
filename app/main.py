from fastapi import FastAPI

app = FastAPI(
    title="Recommendation Platform",
    version="0.1.0",
    description="Production-oriented recommendation and ranking API.",
)


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
