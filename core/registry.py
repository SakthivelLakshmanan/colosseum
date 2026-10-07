"""Adviser registry loader for Colosseum."""
from __future__ import annotations

from pathlib import Path
from typing import Optional
import yaml
from pydantic import BaseModel, Field


class AdviserConfig(BaseModel):
    id: str
    name: str
    enabled: bool = True
    lens: str
    capabilities: list[str] = Field(default_factory=list)
    criteria_owned: list[str] = Field(default_factory=list)
    can_block: bool = False
    model: str = "adviser"
    instructions: str
    fact_domains: list[str] = Field(default_factory=list)
    tools: list[str] = Field(default_factory=list)
    instructions_content: Optional[str] = None


class AdviserRegistry(BaseModel):
    advisers: list[AdviserConfig] = Field(default_factory=list)
    shared_prompt: Optional[str] = None

    def get(self, adviser_id: str) -> Optional[AdviserConfig]:
        """Get an adviser configuration by ID."""
        for adv in self.advisers:
            if adv.id == adviser_id:
                return adv
        return None

    def list_enabled(self) -> list[AdviserConfig]:
        """Return all enabled advisers."""
        return [adv for adv in self.advisers if adv.enabled]

    def get_names(self) -> list[str]:
        """Return list of adviser names."""
        return [adv.name for adv in self.advisers]

    def get_ids(self) -> list[str]:
        """Return list of adviser IDs."""
        return [adv.id for adv in self.advisers]


def find_project_root() -> Path:
    """Resolve the project root directory."""
    return Path(__file__).resolve().parent.parent


def load_registry(
    config_path: str | Path | None = None,
    base_dir: str | Path | None = None,
) -> AdviserRegistry:
    """Load config/advisers.yaml and the prompt files for each adviser."""
    root = Path(base_dir) if base_dir else find_project_root()
    cfg_file = Path(config_path) if config_path else root / "config" / "advisers.yaml"

    if not cfg_file.is_file():
        raise FileNotFoundError(f"Advisers config file not found: {cfg_file}")

    with open(cfg_file, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f)

    advisers_data = data.get("advisers", [])
    advisers: list[AdviserConfig] = []

    for item in advisers_data:
        adv = AdviserConfig.model_validate(item)
        prompt_file = root / adv.instructions
        if prompt_file.is_file():
            adv.instructions_content = prompt_file.read_text(encoding="utf-8")
        else:
            adv.instructions_content = None
        advisers.append(adv)

    shared_prompt_file = root / "prompts" / "advisers" / "_shared.md"
    shared_prompt = (
        shared_prompt_file.read_text(encoding="utf-8")
        if shared_prompt_file.is_file()
        else None
    )

    return AdviserRegistry(advisers=advisers, shared_prompt=shared_prompt)


_registry_instance: Optional[AdviserRegistry] = None


def get_registry() -> AdviserRegistry:
    """Get the cached adviser registry or load it."""
    global _registry_instance
    if _registry_instance is None:
        _registry_instance = load_registry()
    return _registry_instance
