from __future__ import annotations

from ..state.models import SkillCategory, SkillSignature
from .catalog import SKILL_CATEGORIES, SKILL_SIGNATURES


class SkillRegistry:
    def __init__(
        self,
        *,
        categories: list[SkillCategory] | None = None,
        signatures: list[SkillSignature] | None = None,
    ) -> None:
        self._categories = list(categories or SKILL_CATEGORIES)
        self._signatures = list(signatures or SKILL_SIGNATURES)

    def get_categories(self) -> list[SkillCategory]:
        return [item.model_copy(deep=True) for item in self._categories]

    def get_signatures(self, categories: list[str]) -> list[SkillSignature]:
        allowed = {item for item in categories if item}
        return [item.model_copy(deep=True) for item in self._signatures if item.category in allowed]

    def get_signature(self, skill_name: str) -> SkillSignature | None:
        for item in self._signatures:
            if item.name == skill_name:
                return item.model_copy(deep=True)
        return None
