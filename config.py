from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv(Path(__file__).with_name(".env"))


def _clean_env_value(value: str | None) -> str:
    """Strip surrounding whitespace and matching quotes from an env string."""
    if not value:
        return ""
    cleaned = value.strip()
    if len(cleaned) >= 2 and cleaned[0] == cleaned[-1] and cleaned[0] in ('"', "'"):
        cleaned = cleaned[1:-1].strip()
    return cleaned


GROQ_API_KEY: str = (
    _clean_env_value(os.getenv("GROQ_API"))
    or _clean_env_value(os.getenv("GROQ_API_KEY"))
    or ""
)
GROQ_API_URL: str = "https://api.groq.com/openai/v1/audio/transcriptions"
GROQ_MODEL: str = "whisper-large-v3-turbo"
GROQ_FALLBACK_MODEL: str = "whisper-large-v3"
SAMPLE_RATE: int = 16000
CHANNELS: int = 1
