# ADR-011: Privacy-safe recommendation explanations

## Context / Problem

The ranking API returned only item IDs and opaque floating-point scores. Clients could
not distinguish genuine collaborative ranking from a cold-start popularity fallback,
and engineers could not explain which parts of a target user's profile supported a
personalized result. Exposing neighbor user IDs would make debugging easier but would
leak another user's identity through the recommendation API.

## Decision

Return a bounded reason on every recommendation: `similar_users`, `popular`, or
`popularity_fallback`. For collaborative results, also return the number of distinct
target-history items that overlapped with contributing neighbor profiles. Never expose
target-history item IDs, neighbor IDs, or individual neighbor interaction strengths.

Treat explanation fields as part of the cached ranking contract. Validate them when
reading Redis, reject incompatible payloads, and bump both model versions so deployments
cannot serve pre-explanation cache entries. Include the fields in the HTTP smoke test and
reflect the new model versions in the checked evaluation artifact.

## Why this approach

The reason makes fallback behavior explicit instead of presenting all scores as equally
personalized. A shared-item count provides deterministic evidence strength while
minimizing profile disclosure. Computing evidence during ranking reuses the overlap already
needed for cosine similarity and keeps explanations aligned with the actual algorithm.

## Alternatives considered

- **Return neighbor user IDs:** directly traceable but creates an unnecessary privacy and
  authorization risk.
- **Generate natural-language explanations with an LLM:** easier to read, but expensive,
  nondeterministic, and capable of claiming evidence the ranker did not use.
- **Return only the selected strategy:** does not reveal when personalized ranking fell
  back to popularity.
- **Compute explanations after ranking:** separates concerns superficially but risks
  reconstructing different evidence from the data used to score candidates.

## Tradeoffs / risks

An overlap count can still reveal that a profile exists, so production access requires
authentication and authorization. Explanations summarize supporting overlap; they are not
causal claims that a user will engage. Extra fields modestly increase cache and response
size. More detailed evidence should be added only behind an authorized user boundary.

## How it fits the architecture

The recommender owns score and evidence construction, the cache preserves and validates
the complete ranked result, and the API serializes it without reconstructing model logic.
The same model-version boundary that protects scores now protects explanation semantics.

## Interview explanation

"I made fallback behavior visible and added evidence tied to the actual cosine-overlap
calculation. The API reports how many target-profile items supported a result but never
reveals those items or which neighboring users contributed. I versioned and validated the
cache payload because explainability is part of the model contract, not a UI decoration."
