from __future__ import annotations

import httpx

import config


class GroqTranscriber:
    """Speech-to-text client using Groq's OpenAI-compatible Whisper endpoint."""

    def __init__(self, api_key: str | None = None, model: str | None = None) -> None:
        self.api_key = (config.GROQ_API_KEY if api_key is None else api_key).strip()
        chosen_model = config.GROQ_MODEL if model is None else model
        self.model = chosen_model.strip() or config.GROQ_MODEL

    def _redact_secret(self, text: str) -> str:
        if self.api_key and self.api_key in text:
            return text.replace(self.api_key, "[REDACTED]")
        return text

    def transcribe(self, wav_bytes: bytes, language: str | None = None) -> str:
        """Transcribe WAV audio bytes via Groq Whisper; auto-detects language by default."""
        if not wav_bytes:
            return ""
        if not self.api_key:
            raise RuntimeError(
                "Groq API key is missing. Set GROQ_API_KEY (or GROQ_API) in .env."
            )

        headers = {"Authorization": f"Bearer {self.api_key}"}
        files = {"file": ("speech.wav", wav_bytes, "audio/wav")}
        data: dict[str, str] = {
            "model": self.model,
            "response_format": "json",
            "temperature": "0.0",
        }
        if language is not None and language.strip():
            data["language"] = language.strip()

        try:
            with httpx.Client(timeout=30.0) as client:
                resp = client.post(
                    config.GROQ_API_URL,
                    headers=headers,
                    files=files,
                    data=data,
                )
        except httpx.HTTPError as exc:
            detail = self._redact_secret(str(exc))
            raise RuntimeError(f"Groq transcription request failed: {detail}") from None

        if resp.status_code >= 400:
            detail = ""
            try:
                err_body = resp.json()
                if isinstance(err_body, dict):
                    err_obj = err_body.get("error")
                    if isinstance(err_obj, dict):
                        detail = str(err_obj.get("message") or "")
                    elif err_obj:
                        detail = str(err_obj)
            except Exception:
                pass
            if not detail:
                detail = resp.text.strip()[:300]
            detail = self._redact_secret(detail) or resp.reason_phrase or "HTTP error"
            raise RuntimeError(
                f"Groq transcription failed (HTTP {resp.status_code}): {detail}"
            )

        try:
            payload = resp.json()
        except Exception as exc:
            raise RuntimeError(
                f"Invalid JSON response from Groq transcription API: {exc}"
            ) from None

        if not isinstance(payload, dict):
            return ""
        text = payload.get("text", "")
        return text.strip() if isinstance(text, str) else ""
