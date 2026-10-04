# evidence-memory

Wraps SqliteIndex and ranks a current fact above a superseded one in the same subject.

It wins on `stale_at_5` (`uv run iris eval memory --component evidence-memory`). It loses when the stale line and the current line do not share a subject: the ranker then leaves the index order alone.

Requires the sqlite file the rest of the process already uses.
