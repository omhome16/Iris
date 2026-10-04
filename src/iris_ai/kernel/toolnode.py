"""The tools node belongs to the kernel.

`ChatGraph._tools` is that node: policy, guards, the journal, approval, and
dispatch. An engine reaches it only by returning `Step(next=TOOLS)`. This
module is the name other engines import when they need to describe the
boundary. The implementation stays on `ChatGraph` so a pause and a resume
keep the thread shape they already have.
"""

from __future__ import annotations

TOOLS_NODE = "tools"
