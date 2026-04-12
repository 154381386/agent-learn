from __future__ import annotations

from pathlib import Path
from typing import Iterable

import yaml

from ..state.models import SkillCategory, SkillSignature


class SkillPackLoader:
    def __init__(self, packs_root: Path | None = None) -> None:
        self.packs_root = packs_root or Path(__file__).with_suffix("").parent / "packs"

    def load(self) -> tuple[list[SkillCategory], list[SkillSignature]]:
        if not self.packs_root.exists():
            return [], []
        categories: list[SkillCategory] = []
        signatures: list[SkillSignature] = []
        for manifest_path in sorted(self.packs_root.glob('*/skill.yaml')):
            loaded_categories, loaded_signatures = self._load_manifest(manifest_path)
            categories.extend(loaded_categories)
            signatures.extend(loaded_signatures)
        return categories, signatures

    def _load_manifest(self, manifest_path: Path) -> tuple[list[SkillCategory], list[SkillSignature]]:
        payload = yaml.safe_load(manifest_path.read_text(encoding='utf-8')) or {}
        pack_name = str(payload.get('name') or manifest_path.parent.name)
        guide_path = manifest_path.parent / 'SKILL.md'
        guide_text = guide_path.read_text(encoding='utf-8').strip() if guide_path.exists() else ''
        guide_summary = _extract_guide_summary(guide_text)

        categories: list[SkillCategory] = []
        category_payloads: list[dict] = []
        if isinstance(payload.get('category'), dict):
            category_payloads.append(dict(payload['category']))
        if isinstance(payload.get('categories'), list):
            category_payloads.extend([dict(item) for item in payload['categories'] if isinstance(item, dict)])
        for item in category_payloads:
            categories.append(SkillCategory.model_validate(item))

        signatures: list[SkillSignature] = []
        for item in payload.get('skills') or []:
            if not isinstance(item, dict):
                continue
            data = dict(item)
            data.setdefault('pack_name', pack_name)
            data.setdefault('sop_summary', guide_summary or str(payload.get('description') or ''))
            data.setdefault('guide_path', str(guide_path.relative_to(manifest_path.parents[2])) if guide_path.exists() else '')
            signatures.append(SkillSignature.model_validate(data))
        return categories, signatures


def _extract_guide_summary(text: str) -> str:
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith('#') or stripped.startswith('- ') or stripped.startswith('```'):
            continue
        return stripped
    return ''
