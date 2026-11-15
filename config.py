from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

ENV_PATH: Path = Path(__file__).with_name(".env")
load_dotenv(ENV_PATH)


def _clean_env_value(value: str | None) -> str:
    """Strip surrounding whitespace and matching quotes from an env string."""
    if not value:
        return ""
    cleaned = value.strip()
    if len(cleaned) >= 2 and cleaned[0] == cleaned[-1] and cleaned[0] in ('"', "'"):
        cleaned = cleaned[1:-1].strip()
    return cleaned


def save_groq_api_key(api_key: str, env_path: Path | None = None) -> str:
    """Persist a cleaned Groq API key into .env and update runtime config."""
    global GROQ_API_KEY
    if "\r" in api_key or "\n" in api_key:
        raise ValueError("API key must be a single line.")
    cleaned = _clean_env_value(api_key)
    if not cleaned:
        raise ValueError("API key cannot be empty.")

    target = Path(env_path) if env_path is not None else ENV_PATH
    existing_lines: list[str] = []
    if target.exists():
        raw = target.read_text(encoding="utf-8-sig")
        existing_lines = raw.splitlines()

    new_lines: list[str] = []
    wrote_key = False
    for line in existing_lines:
        stripped = line.lstrip()
        if not stripped.startswith("#") and "=" in stripped:
            key_name = stripped.split("=", 1)[0].strip()
            if key_name in ("GROQ_API_KEY", "GROQ_API"):
                if not wrote_key:
                    new_lines.append(f"GROQ_API_KEY={cleaned}")
                    wrote_key = True
                continue
        new_lines.append(line)

    if not wrote_key:
        new_lines.append(f"GROQ_API_KEY={cleaned}")

    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("\n".join(new_lines) + "\n", encoding="utf-8")

    if env_path is None or target.resolve() == ENV_PATH.resolve():
        os.environ["GROQ_API_KEY"] = cleaned
        os.environ.pop("GROQ_API", None)
        GROQ_API_KEY = cleaned

    return cleaned


GROQ_API_KEY: str = (
    _clean_env_value(os.getenv("GROQ_API"))
    or _clean_env_value(os.getenv("GROQ_API_KEY"))
    or ""
)
GROQ_API_URL: str = "https://api.groq.com/openai/v1/audio/transcriptions"
GROQ_SIGNUP_URL: str = "https://console.groq.com/keys"
GROQ_MODEL: str = "whisper-large-v3-turbo"
GROQ_FALLBACK_MODEL: str = "whisper-large-v3"
SAMPLE_RATE: int = 16000
CHANNELS: int = 1
