# ADR-010: Versioned, reproducible evaluation artifacts

## Context / Problem

Offline metrics are only interpretable when the exact data, split procedure, and
ranking implementations are identifiable. The evaluator printed model versions,
but two reports from different fixtures looked structurally identical, and CI did
not prove that the checked sample and documented command still produced the same
result.

## Decision

Emit a schema-versioned JSON envelope containing separate canonical SHA-256 identities
for the interaction snapshot and optional item catalog, their counts, the named split
version, K, and each ranker's model version. The interaction fingerprint includes UTC
event time and duplicate counts while remaining independent of export row order. The
catalog fingerprint covers the ranking-relevant item ID, category, and active flag;
display-title edits do not create a false model-data change. Check in the sample
fixtures' generated report and have CI regenerate it and fail on any diff.

## Why this approach

Content addressing ties metrics to data without relying on filenames, timestamps,
or mutable database state. Explicit schema, split, and model versions identify all
major inputs to the deterministic calculation. Separating the two fingerprints makes
it clear whether behavior changed because of feedback data or candidate eligibility and
taxonomy. A plain JSON artifact is reviewable, portable, and easy for later experiment
tooling to consume.

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

The interaction fingerprint ignores source row order but includes event timestamps;
changing event time therefore changes the data identity used by the temporal split and
decay policy. Catalog titles are excluded because they enrich responses but do not alter
ranking. Any future title-aware model must expand and version this contract. The sample
report demonstrates reproducibility only; its metrics do not establish production
quality or business impact.

## How it fits the architecture

Evaluation remains an offline, side-effect-free path over fixture data. It imports
the same ranking implementations used by the API, records their explicit model
versions, and does not access the repository or cache boundaries. Schema version 3 adds
catalog provenance and category-policy metrics while keeping the evaluator side-effect
free. CI connects both fixtures, evaluator, report, and documentation through one
deterministic check.

## Interview explanation

"A metric without data and model identity is not reproducible evidence. I content-hash
the timestamped interaction multiset and the ranking-relevant catalog separately, record
the split, policy, and model versions, and make CI reproduce the JSON byte for byte. That
makes both feedback drift and catalog-policy changes reviewable while staying honest that
the synthetic fixture is a correctness demo, not a claim about real-world lift."
