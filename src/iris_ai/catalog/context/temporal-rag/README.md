# temporal-rag

Puts the newest fact in a recall block and older overlaps in a history block.

It wins on `stale_as_current` (`uv run iris eval context --component temporal-rag`). It loses on tokens: the history block is extra text the default does not render.

Requires `memory.read`.
