# Production Recommendation Platform

FastAPI service with weighted popularity, user cosine similarity, interaction
storage (memory or SQL), and versioned recommendation caching (memory or Redis).

Cache entries include the selected ranker's explicit `model_version`. Changing
ranking semantics and bumping that version produces a cache miss instead of
serving scores created by the previous algorithm. The configured cache namespace
still provides a deployment-wide compatibility boundary. See
[ADR-007](docs/architecture/ADR-007-model-versioned-cache-keys.md).

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
`/metrics` exposes Prometheus-compatible request, latency, cache, and ranking
metrics. Every HTTP response includes a generated `X-Request-ID`, and completion
logs carry the same ID as structured JSON for correlation. Health, readiness,
and scrape traffic are excluded from request-rate and latency metrics.
See `.env.example` for optional PostgreSQL and Redis settings. Default memory
storage is process-local and loses interactions on restart.

For retry-safe ingestion, send a stable `Idempotency-Key` header with each logical
event. The first request returns `201` with `status: recorded`; an exact retry
returns `200` with `status: replayed` and does not duplicate the interaction. Reusing
the same key for a different payload returns `409`. Requests without the header keep
append-only event behavior. SQL deployments store idempotency records durably and
atomically with the interaction. See
[ADR-009](docs/architecture/ADR-009-idempotent-interaction-ingestion.md).

## Run the complete stack

```bash
docker compose up --build --detach --wait
python scripts/smoke_test.py
```

This starts the non-root API container with PostgreSQL and Redis, waits for all
health checks, and verifies the end-to-end REST flow. The API binds to
`http://127.0.0.1:8000`. See [the deployment guide](docs/deployment.md) for
configuration, hosted deployment, verification, rollback, and known limits.

## Seed the SQL demo database

After `docker compose up --build --detach --wait`, load the checked-in
synthetic sample fixture into the **Compose demo database**:

```bash
docker compose exec -T api python -m scripts.seed_sample_data
# Safe to rerun: if the exact fixture already exists, no events are duplicated.
docker compose exec -T api python -m scripts.seed_sample_data
curl -fsS 'http://127.0.0.1:8000/recommendations/alice?strategy=personalized&limit=5'
```

The seeder requires `DATABASE_URL` and refuses to write into a nonempty database
unless its complete interaction sequence already matches the fixture. This avoids
silently mixing demo interactions into an existing corpus. Use it only on an
isolated demo database. Each fixture insert happens in one SQL transaction.
The API smoke test still uses fresh isolated IDs and works after seeding.

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

## Continuous integration

GitHub Actions runs unit tests and Ruff on every push and pull request. A second
job builds the image, starts the PostgreSQL and Redis stack, runs the REST smoke
test, captures logs on failure, and removes its volumes afterward.

## Observability

Scrape `GET /metrics` with Prometheus or any OpenMetrics-compatible collector.
The custom metrics use bounded labels only: HTTP route templates rather than raw
paths, fixed ranking strategies, explicit model versions, and cache outcomes.
This keeps user IDs and item IDs out of telemetry and prevents unbounded series.
See [ADR-008](docs/architecture/ADR-008-request-observability.md) for the design
and operational tradeoffs.
