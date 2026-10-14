"""Configuration loader for Colosseum settings."""
from __future__ import annotations

from pathlib import Path
from typing import Optional
import yaml
from pydantic import BaseModel, Field


class ModelsConfig(BaseModel):
    adviser: str = "gemini-3.8-flash"
    reasoning: str = "gemini-3.1-pro-preview"
    embedding: str = "gemini-embedding-001"


class GeminiConfig(BaseModel):
    max_concurrent: int = 6
    timeout_s: float = 60.0
    max_retries: int = 3
    retry_backoff: list[float] = Field(default_factory=lambda: [2.0, 8.0, 20.0])


class AdvisersSettingsConfig(BaseModel):
    timeout_s: float = 420.0


class BigQueryConfig(BaseModel):
    location: str = "asia-south1"
    dataset: str = "colosseum"
    eval_dataset: str = "colosseum_eval"


class AgentSearchConfig(BaseModel):
    location: str = "global"
    engine_id: Optional[str] = None
    data_store_id: Optional[str] = None


class DocumentsConfig(BaseModel):
    bucket: str = ""
    prefix: str = "docs/"
    agent_search: AgentSearchConfig = Field(default_factory=AgentSearchConfig)


class FirestoreCollectionsConfig(BaseModel):
    runs: str = "runs"
    routing: str = "routing_logs"
    decisions: str = "human_decisions"


class FirestoreConfig(BaseModel):
    database: str = "(default)"
    collections: FirestoreCollectionsConfig = Field(default_factory=FirestoreCollectionsConfig)


class RetrievalConfig(BaseModel):
    max_facts_per_adviser: int = 25
    include_related_domains: bool = True
    exclude_superseded: bool = True
    ranking: str = "keyword"


class AggregationConfig(BaseModel):
    strategy: str = "latest_per_metric_then_summarise"
    summary_model: str = "adviser"
    keep_fact_ids_in_summaries: bool = True


class RunConfig(BaseModel):
    parallel_advisers: bool = True
    adviser_timeout_s: int = 420
    continue_if_adviser_fails: bool = True


class Settings(BaseModel):
    project_id: str
    models_location: str = "global"
    models: ModelsConfig = Field(default_factory=ModelsConfig)
    gemini: GeminiConfig = Field(default_factory=GeminiConfig)
    advisers: AdvisersSettingsConfig = Field(default_factory=AdvisersSettingsConfig)
    bigquery: BigQueryConfig = Field(default_factory=BigQueryConfig)
    documents: DocumentsConfig = Field(default_factory=DocumentsConfig)
    firestore: FirestoreConfig = Field(default_factory=FirestoreConfig)
    retrieval: RetrievalConfig = Field(default_factory=RetrievalConfig)
    aggregation: AggregationConfig = Field(default_factory=AggregationConfig)
    run: RunConfig = Field(default_factory=RunConfig)


_settings_instance: Optional[Settings] = None


def find_config_path(filename: str = "settings.yaml") -> Path:
    """Locate config file from current working directory or repository root."""
    candidates = [
        Path("config") / filename,
        Path(__file__).resolve().parent.parent / "config" / filename,
        Path(filename),
    ]
    for candidate in candidates:
        if candidate.is_file():
            return candidate
    raise FileNotFoundError(f"Config file not found: {filename}")


def load_settings(path: str | Path | None = None) -> Settings:
    """Load settings from YAML into a typed Settings object."""
    global _settings_instance
    config_file = Path(path) if path else find_config_path("settings.yaml")
    with open(config_file, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f)
    _settings_instance = Settings.model_validate(data)
    return _settings_instance


def get_settings() -> Settings:
    """Get the cached settings instance or load it from the default path."""
    global _settings_instance
    if _settings_instance is None:
        _settings_instance = load_settings()
    return _settings_instance
