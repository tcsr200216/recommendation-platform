# ADR-005: Containerized three-service deployment topology

Status: Accepted

## Context

The API already supports durable SQL interaction storage and a shared Redis
cache, but the repository did not provide a reproducible way to run or verify
those components together. A hostable portfolio application needs one command
for a production-like environment and an automated check of its real REST path.

## Decision

Package the FastAPI service in a Python 3.12 slim image that runs as UID/GID
10001. Run it with PostgreSQL 16 and Redis 7 through Docker Compose. Keep data
services on the internal network, expose the API only on host loopback for local
development, and gate API startup and health on dependency readiness.

Add a standard-library smoke test that waits for `/ready`, records unique
interactions through the public API, and verifies a personalized result. Run
unit tests and lint in one CI job and the complete container stack plus smoke
test in another. Tear down volumes after CI regardless of outcome.

## Consequences

Developers and CI now exercise the same service topology, including PostgreSQL,
Redis, cache invalidation, and HTTP serialization. The image has a smaller
privilege surface because the process does not run as root. Health checks give
orchestrators explicit liveness and dependency readiness signals.

Compose provides reproducibility rather than high availability. Its example
credentials are local-only, it runs one API replica, and it does not configure
TLS, backups, monitoring, resource limits, or secret rotation. Hosted platforms
must supply those controls. The application also creates its current table
additively at startup; schema evolution needs explicit migrations before the
first incompatible database change.

## Alternatives considered

- A single container with embedded data services would be easier to start but
  would couple lifecycles and make persistence and scaling brittle.
- Memory-only deployment would avoid infrastructure but lose interactions and
  shared cache state during restart or horizontal scaling.
- Kubernetes manifests would add operational surface before the service needs
  multi-node orchestration. The container contract can be used by Kubernetes or
  another platform later without changing application code.
