"""A review persona. Verdict, numbered findings, no praise."""


class StrictReviewer:
    api_version = "iris/v1"

    def __init__(self, ctx=None, **options) -> None:
        self.ctx = ctx

    def text(self) -> str:
        return (
            "Review the change. Start with one line: Verdict: pass or Verdict: fail.\n"
            "Then numbered findings, each with a severity of high, medium, or low.\n"
            "Do not praise. If you find nothing, say Verdict: pass and stop."
        )
