# Demo

Ninety seconds. Record it on Linux or WSL2 so `iris components check` prints
`isolation=landlock+seccomp+netns`. The same commands on Windows print
`isolation=audit`, which is the honest label and the wrong frame for the video.

1. `iris chat`, then a persona Iris wrote for itself. `iris components check persona <name>` shows the jail. Approve it. `/explain` names the persona, the model, and the cost.
2. `iris components use persona strict-reviewer`, then `/reload`. Ask it to review a three-line diff. The reply starts with `Verdict:`.
3. `iris eval context --component default` then `iris eval context --component temporal-rag`. The stale-fact number drops. `iris components use context temporal-rag`.
4. `iris components use capture decision-only`. A question writes nothing. "From now on I use uv" writes a candidate.
5. Two live preferences about the same subject. `iris memory conflicts` lists one open conflict. Nothing in MEMORY.md changed until you resolve it.
6. The same session id from Discord (`examples/iris-discord`) and from `iris chat`. `/explain` shows the channel and the origin.
7. `iris components use engine plan-execute`. A short note names the node. The tool still pauses for approval in the kernel.
8. One `evolve` summary: the frontier, the held-out numbers, and the line that nothing activated. The owner runs `iris components use` themselves.

Closing card: THE MODEL DIDN'T CHANGE. THE HARNESS DID.
