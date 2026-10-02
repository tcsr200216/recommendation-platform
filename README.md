# Production Recommendation Platform

FastAPI service with weighted popularity, user cosine similarity, interaction
storage (memory or SQL), and versioned recommendation caching (memory or Redis).

## Run locally

Use Python 3.12+. From the repository root:

```bash
python -m venv .venv
source .venv/bin/activate
python -m pip install -e '.[dev]'
uvicorn app.main:app --reload
```

Open `/docs` for the interactive API. Record events with `POST /interactions`,
then call `GET /recommendations/{user_id}?strategy=personalized&limit=10`.
`/health` checks liveness; `/ready` checks storage and cache dependencies.
See `.env.example` for optional PostgreSQL and Redis settings. Default memory
storage is process-local and loses interactions on restart.

## Offline evaluation

```bash
python -m app.evaluation data/sample_interactions.json --k 5
python -m pytest
```

The CLI compares both strategies on the same deterministic leave-one-out split.
It removes every event for the lexicographically last distinct item of each
user with at least two distinct items. The remaining events train the models.
No live database or cache is read or modified. This is an item-based split,
not a chronological split: the current interaction schema has no timestamps.

HitRate@K is the fraction of evaluated users whose held-out item appears in the
top K. MRR@K averages its reciprocal rank (zero for a miss). Sparse users remain
in training but are excluded from the metric denominator and counted explicitly.
Targets absent from the training catalog count as misses and are reported as
`cold_target_users`. With no eligible users, metrics are JSON null.
Reports include strategy, algorithm version, K, and user counts.

The synthetic fixture exercises repeated events, overlapping interests, sparse
users, and unseen targets. Its scores are a reproducibility demonstration, not
evidence of production quality or business impact. See
[ADR-004](docs/architecture/ADR-004-offline-evaluation.md) for tradeoffs.
