"""Settings from environment variables (set on the AgentCore Runtime by infra/deploy.py)."""
import os
from dataclasses import dataclass


@dataclass(frozen=True)
class Config:
    gateway_url: str = os.environ.get("GATEWAY_URL", "")
    region: str = os.environ.get("AWS_REGION", os.environ.get("AWS_DEFAULT_REGION", "ap-southeast-1"))
    model_id: str = os.environ.get("MODEL_ID", "")
    memory_id: str = os.environ.get("MEMORY_ID", "")          # empty: no short-term memory
    history_turns: int = int(os.environ.get("MEMORY_HISTORY_TURNS", "6"))
    target_name: str = os.environ.get("GATEWAY_TARGET_NAME", "CorpOnePortal")
    max_tokens: int = int(os.environ.get("MAX_TOKENS", "1500"))


def load() -> Config:
    return Config()
