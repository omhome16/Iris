# conflict-resolver

Turns an addition that contradicts a live curated fact into a conflict. The owner resolves it. The kernel writes.

It wins on `silent_overwrite` (`uv run iris eval consolidator --component conflict-resolver`). It loses when two lines share a common word but are not about the same decision: the overlap rule can flag a false conflict.

Resolve with `iris memory conflicts ID --resolve keep|replace|both` or `/conflicts`.
