# Deployment guide

## Local production-like stack

The Compose stack runs the API with PostgreSQL for durable interactions and
Redis for shared recommendation caching. Docker Compose 2.20 or newer is
recommended because startup uses dependency health checks and `--wait`.

```bash
docker compose up --build --detach --wait
python scripts/smoke_test.py
docker compose down
```

The API is available at `http://127.0.0.1:8000`; OpenAPI documentation is at
`/docs`. PostgreSQL and Redis stay on the internal Compose network. Named
volumes preserve their data across normal restarts. Use `docker compose down
--volumes` only when you intentionally want to delete local data.

## Runtime contract

The container listens on port 8000 and runs as the unprivileged UID/GID 10001.
`GET /health` is the process liveness endpoint. `GET /ready` checks the selected
interaction repository and recommendation cache. Route traffic only after
readiness succeeds, and stop routing before terminating an instance.

For a hosted deployment, provide these environment variables through the
platform's secret/configuration system:

- `DATABASE_URL`: SQLAlchemy PostgreSQL URL, including credentials.
- `REDIS_URL`: Redis connection URL, including TLS and credentials when required.
- `CACHE_TTL_SECONDS`: positive result-cache lifetime in seconds.
- `CACHE_NAMESPACE`: versioned namespace; change it when ranking or payload
  compatibility changes.

Do not copy a populated `.env` file into the image. Terminate TLS at the load
balancer or ingress, restrict database and Redis network access to the service,
and use managed backups and credential rotation for hosted environments.

## Verification and rollback

The smoke test waits for readiness, records isolated interactions, confirms that
timezone-normalized event times survive ingestion, and verifies that personalized
ranking returns the expected candidate. It uses unique IDs, so it is safe to repeat
against a nonempty test environment.

Build immutable images tagged with the Git commit SHA. Deploy the new image,
wait for readiness, then run the smoke test against a staging URL. Roll back by
redeploying the previous image tag. The current schema is created additively by
SQLAlchemy, including idempotent nullable event-time columns for existing PostgreSQL
databases; introduce a migration tool before making destructive or multi-step schema
changes.

## Known scaling limits

The service rebuilds recommendation profiles from all interactions on each
cache miss. A new interaction invalidates the entire cache namespace. Those
choices are correct for this project stage but will become expensive at higher
traffic. Move ranking to versioned offline snapshots or incremental workers
before scaling writes or the interaction corpus substantially.
