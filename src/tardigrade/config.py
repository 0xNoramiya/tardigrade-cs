from __future__ import annotations

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # TrueFoundry AI Gateway
    tfy_api_key: str = ""
    tfy_host: str = ""
    tfy_gateway_base_url: str = ""

    # Virtual Model in TF (provider-account/virtual-model name)
    tardigrade_primary_model: str = "tardigrade-primary/tardigrade-primary"

    # TrueFoundry MCP Gateway (Virtual MCP Server endpoint)
    tfy_mcp_gateway_url: str = ""

    # AWS Bedrock (optional — only when not routed through TF)
    aws_region: str = "us-east-1"

    # Embeddings tier
    tardigrade_faq_path: str = "data/faq.json"
    tardigrade_embedding_model: str = "sentence-transformers/all-MiniLM-L6-v2"
    tardigrade_embedding_threshold: float = 0.55

    # Production guardrail — when true, chaos is hard-disabled at every layer
    tardigrade_disable_chaos: bool = False


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
