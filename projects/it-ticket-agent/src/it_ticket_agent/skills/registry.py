from __future__ import annotations

from pathlib import Path

from ..state.models import SkillCategory, SkillSignature
from .catalog import SKILL_CATEGORIES, SKILL_SIGNATURES
from .loader import SkillPackLoader


class SkillRegistry:
    def __init__(
        self,
        *,
        categories: list[SkillCategory] | None = None,
        signatures: list[SkillSignature] | None = None,
        packs_root: Path | None = None,
    ) -> None:
        base_categories = list(categories or SKILL_CATEGORIES)
        base_signatures = list(signatures or SKILL_SIGNATURES)
        loaded_categories, loaded_signatures = SkillPackLoader(packs_root).load()
        self._categories = _merge_categories(base_categories, loaded_categories)
        self._signatures = _merge_signatures(base_signatures, loaded_signatures)

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


def _merge_categories(base: list[SkillCategory], loaded: list[SkillCategory]) -> list[SkillCategory]:
    merged: dict[str, SkillCategory] = {item.name: item for item in base}
    for item in loaded:
        previous = merged.get(item.name)
        if previous is None:
            merged[item.name] = item
            continue
        merged[item.name] = previous.model_copy(update={
            'description': item.description or previous.description,
            'skill_count': max(previous.skill_count, item.skill_count),
            'match_keywords': list(dict.fromkeys([*previous.match_keywords, *item.match_keywords])),
        })
    return list(merged.values())


def _merge_signatures(base: list[SkillSignature], loaded: list[SkillSignature]) -> list[SkillSignature]:
    merged: dict[str, SkillSignature] = {item.name: item for item in base}
    for item in loaded:
        merged[item.name] = item
    return list(merged.values())
