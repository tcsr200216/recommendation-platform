# ADR-006: Guarded transactional sample seeding

## Context / Problem

The offline evaluation fixture lives in a JSON file, but a reviewer running the
container stack has no safe way to load that data into the live SQL-backed demo.
Blindly replaying it would duplicate events and inflate popularity statistics.

## Decision

Package the checked-in fixture with the API image and add a CLI seeder that requires
an explicitly configured `DATABASE_URL`. Validate each event before writing.
If the SQL database is empty, insert the entire fixture with one transaction via
`SqlInteractionRepository.add_many`; if it exactly matches the fixture, do nothing.
Reject every other nonempty state without merging or deleting existing events.
Run this path twice in CI before the HTTP smoke test.

## Why this approach

A single transactional batch avoids half-seeded demonstrations, and the guard
prevents accidental mixing of synthetic events with unrelated data. An exact-repeat
no-op offers reproducibility without pretending interaction events are generally
deduplicated at the serving layer.

## Alternatives considered

- Replay the fixture through the public POST endpoint: exercises HTTP but creates
  duplicates on every run and can leave partially loaded data.
- Truncate the database and reseed: convenient for demos but unsafe around user data.
- Seed process-local memory in a separate Python process: cannot affect the running
  API's in-memory repository.
- Silently skip interactions sharing a user/item pair: incorrectly changes the
  fixture, which intentionally contains multiple interaction types for some pairs.

## Tradeoffs / Risks

The empty/exact-match guard is intended for a single-operator demo environment;
it is not a concurrency-safe generalized import service. The fixture is synthetic
and not evidence of real recommendation quality. The command must never be run
against a production database without explicit review.

## How it fits the architecture

The JSON fixture remains the offline-evaluation source. The seeder converts it to
domain interactions and calls the SQL repository's atomic batch adapter. The
running API then reads the same persisted events to serve recommendation requests.
No new database schema is needed.

## Interview explanation

"I built a reproducible seed path that inserts a whole synthetic fixture in one
SQL transaction. Rerunning it on the same fixture does nothing, but the tool refuses
to contaminate a database containing unrelated events. That gives reviewers a
reliable end-to-end demo and keeps seed data distinct from real production data."
