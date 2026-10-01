from pathlib import Path
from typing import Any

import yaml
from pydantic import Field

from figma_to_fgui.models import FrozenModel


class Rule(FrozenModel):
    id: str
    version: int
    priority: int
    when: dict[str, Any] = Field(default_factory=dict)
    action: dict[str, str]
    confidence: float


def load_rules(path: Path) -> tuple[Rule, ...]:
    data = yaml.safe_load(path.read_text("utf-8"))
    rules = (Rule.model_validate(item) for item in data["rules"])
    return tuple(sorted(rules, key=lambda rule: (-rule.priority, rule.id)))
