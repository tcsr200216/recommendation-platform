# ADR-010: Versioned, reproducible evaluation artifacts

## Context / Problem

Offline metrics are only interpretable when the exact data, split procedure, and
ranking implementations are identifiable. The evaluator printed model versions,
but two reports from different fixtures looked structurally identical, and CI did
not prove that the checked sample and documented command still produced the same
result.

## Decision

Emit a schema-versioned JSON envelope containing a canonical dataset SHA-256,
interaction count, named split version, K, and each ranker's model version. The
dataset fingerprint sorts unique `(user, item, interaction type)` tuples and
includes their occurrence counts, making it independent of export row order while
retaining repeated-event semantics. Check in the sample fixture's generated report
and have CI regenerate it and fail on any diff.

## Why this approach

Content addressing ties metrics to data without relying on filenames, timestamps,
or mutable database state. Explicit schema, split, and model versions identify all
major inputs to the deterministic calculation. A plain JSON artifact is reviewable,
portable, and easy for later experiment tooling to consume.

## Alternatives considered

- **Hash the source file bytes:** simple, but semantically identical exports with
  different row order or formatting would receive different identities.
- **Use a timestamp or Git commit only:** useful provenance, but neither identifies
  the evaluated data content and timestamps make output nondeterministic.
- **Store reports only in CI artifacts:** avoids repository changes, but makes drift
  harder to review and historical evidence dependent on artifact retention.
- **Write evaluation runs to PostgreSQL:** appropriate for a mature experiment
  service, but adds infrastructure and coupling that this deterministic CLI does
  not need yet.

## Tradeoffs / risks

The fingerprint deliberately ignores event order because the current interaction
domain has no timestamp and both rankers are order-independent. If temporal ranking
or splitting is introduced, the data-version contract must change and the split
version must be bumped. The sample report demonstrates reproducibility only; its
metrics do not establish production quality or business impact.

## How it fits the architecture

Evaluation remains an offline, side-effect-free path over fixture data. It imports
the same ranking implementations used by the API, records their explicit model
versions, and does not access the repository or cache boundaries. CI connects the
fixture, evaluator, report, and documentation through one deterministic check.

## Interview explanation

"A metric without data and model identity is not reproducible evidence. I added a
canonical content hash for the interaction multiset, explicit report/split/model
versions, and a CI-regenerated JSON artifact. That makes ranking changes visible in
code review while being honest that the synthetic fixture is a correctness demo,
not an offline claim about real-world lift."
