# ADR-013: Deterministic time-decayed interaction strength

## Context / Problem

The platform persists timezone-safe interaction timestamps and uses them for temporal
offline holdouts, but live ranking still weights an old event exactly like a recent
event of the same type. User interests and item popularity change over time, so an
indefinitely strong historical purchase can dominate newer clicks or likes. Using the
server clock directly would make offline reports and repeated builds nondeterministic.

## Decision

Apply exponential decay to every timestamped interaction before popularity aggregation
or collaborative profile construction:

`effective strength = event weight × 0.5^(age / 30 days)`

Age is measured from the newest timestamp in the repository snapshot, not wall-clock
time. This makes rankings deterministic for a fixed dataset and preserves relative
event recency. Personalized profiles still keep the strongest effective signal for a
user/item pair, preventing duplicate events from inflating a profile. Undated legacy
events retain their original event weights because the system cannot safely invent
their age.

Both model versions are bumped and include the fixed 30-day decay policy. The cache
already includes model version in its key, so deployments cannot serve rankings
created by the non-decayed models after this change.

## Why this approach

Exponential decay is explainable, continuous, inexpensive, and gives the half-life a
clear interpretation: after 30 days an event contributes half its previous strength.
Anchoring to the newest event makes unit tests, checked evaluation artifacts, and
historical replays reproducible. Applying decay before cosine similarity lets recency
affect both the target profile and neighbor candidate evidence rather than only acting
as a final global reranker.

## Alternatives considered

- **Keep lifetime weights:** deterministic and simple, but cannot adapt to changing
  interests or popularity.
- **Use request time as the reference:** natural for serving, but makes offline output
  drift over time and complicates cache semantics.
- **Hard recency windows:** simple, but creates an abrupt boundary where nearly equal
  events receive completely different treatment.
- **Learn decay per user or event type:** potentially stronger, but requires enough
  representative data and a training/tuning process that this project does not claim.
- **Discard undated events:** temporally pure, but would silently erase legacy behavior
  from live rankings.

## Tradeoffs / risks

Thirty days is an explicit starting policy, not a proven optimum. Undated events do not
decay and may dominate a mixed legacy/timestamped dataset. The newest-event anchor
preserves reproducibility but does not model a period with no incoming events. Large
historical datasets still require precomputation because decay does not reduce the
current per-request scan and similarity cost. Offline metrics must be interpreted
carefully; an unchanged or improved synthetic score is not evidence of production lift.

## How it fits the architecture

Decay stays inside the ranking boundary. Persistence continues to supply normalized
UTC timestamps, evaluation uses the same recommender implementations as serving, and
the model-versioned cache automatically isolates old results. The REST response and
explanation contract do not change because scores remain relative ranking signals.

## Interview explanation

I noticed that adding timestamps only to ingestion and evaluation left a mismatch: the
live model still treated all history as equally current. I introduced a deterministic
30-day exponential half-life and apply it before popularity aggregation and user-vector
similarity. Using the dataset's newest timestamp instead of the wall clock keeps replay
and CI results reproducible. I versioned both models so cached pre-decay results cannot
leak across the change, and I explicitly documented the cold-data and tuning limits.
