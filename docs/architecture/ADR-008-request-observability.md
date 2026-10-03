# ADR-008: Bounded-cardinality request observability

## Context / Problem

Health checks answer whether the API is alive now, but they do not explain a
latency regression, rising error rate, ineffective cache, or a ranking path that
is unexpectedly recomputing every request. Raw user and item identifiers cannot
be metric labels because their unbounded cardinality would make the telemetry
backend expensive and unreliable.

## Decision

The API exposes Prometheus text metrics at `/metrics` and installs one HTTP
middleware that generates a request ID, records request count and duration, and
emits a structured completion log. Application counters separately describe
cache operation outcomes and whether each ranking response came from cache or a
live model execution.

HTTP metrics use the matched FastAPI route template, such as
`/recommendations/{user_id}`, never the raw path. Ranking labels are restricted
to strategy, explicit model version, and `live` or `cache` source. User IDs,
item IDs, and request IDs never become metric labels. Liveness, readiness, and
metrics scrapes are excluded from HTTP traffic totals.

## Why this approach

- Prometheus exposition is portable across local Docker, managed collectors,
  and common container platforms.
- Route templates and small enumerations preserve useful breakdowns without
  creating unbounded time series.
- Request IDs connect an individual response to its structured completion log.
- Cache and ranking counters make optimization behavior visible independently
  of general HTTP success rates.

## Alternatives considered

- **Logs only:** easy to start, but aggregation and alerting require repeatedly
  parsing every event and cache effectiveness is harder to query.
- **OpenTelemetry SDK and collector:** broader distributed-tracing support, but
  introduces collector configuration before this single-service architecture
  has a downstream trace boundary.
- **Raw paths as labels:** appears more detailed but leaks identifiers into
  telemetry and creates unbounded cardinality.

## Tradeoffs / risks

The default Prometheus registry is process-local. Multi-worker deployments must
use a supported aggregation mode or let the collector scrape each worker. The
current request ID is generated at the service boundary rather than trusting an
incoming value, which prevents spoofed correlation but does not yet propagate a
caller trace. Metrics describe service behavior, not recommendation relevance;
offline evaluation remains the quality signal.

## How it fits the architecture

The middleware observes the HTTP boundary without coupling handlers to logging.
Domain-adjacent cache and ranking counters are updated where their outcomes are
known. The existing repository, cache, and recommender boundaries remain
unchanged.

## Interview explanation

“I added request IDs, structured completion logs, and Prometheus metrics, then
kept every label bounded by using route templates and model metadata instead of
user IDs. That lets an operator graph p95 latency, error rate, cache hit behavior,
and live ranking volume without a cardinality explosion or PII-like identifiers
in the metrics backend.”
