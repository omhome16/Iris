"""Setup as data: a plan, a live check, then a write.

The prompts live in `iris_ai.cli.ask`. This package does not print.
"""

from iris_ai.setup.flow import SetupPlan, collect
from iris_ai.setup.verify import verify_model

__all__ = ["SetupPlan", "collect", "verify_model"]
