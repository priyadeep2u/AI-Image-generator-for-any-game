"""Thin wrapper around Ollama's local OpenAI-compatible image generation endpoint."""

from __future__ import annotations

import base64
from pathlib import Path

from openai import APIConnectionError, APIStatusError, OpenAI

from . import config


class OllamaImageClient:
    """Generates images by calling a locally running `ollama serve` instance."""

    def __init__(self, base_url: str = config.OLLAMA_BASE_URL, model: str = config.IMAGE_MODEL):
        self.model = model
        self._client = OpenAI(base_url=base_url, api_key="ollama")

    def generate(self, prompt: str, size: str = config.DEFAULT_SIZE) -> bytes:
        """Generate one image for `prompt` and return the raw PNG bytes."""
        try:
            raw = self._client.images.with_raw_response.generate(
                model=self.model,
                prompt=prompt,
                size=size,
                response_format="b64_json",
            )
        except APIConnectionError as exc:
            raise RuntimeError(
                "Couldn't reach Ollama at the configured URL.\n"
                "    Make sure the Ollama app is running, or start it with: ollama serve"
            ) from exc
        except APIStatusError as exc:
            raise RuntimeError(self._friendly_status_error(exc)) from exc

        if not raw.content.strip():
            raise RuntimeError(
                f"Ollama returned an empty response (HTTP {raw.status_code}) instead of "
                f"an image.\n"
                f"    This almost always means the model '{self.model}' isn't pulled, "
                f"or it crashed/timed out while loading.\n"
                f"    1) Check it's actually installed: ollama list\n"
                f"    2) If missing:                    ollama pull {self.model}\n"
                f"    3) If it IS listed, run `ollama serve` in its own terminal and "
                f"retry — the real error will print there."
            )

        try:
            parsed = raw.parse()
        except Exception as exc:
            raise RuntimeError(
                f"Ollama returned something that wasn't valid JSON (HTTP {raw.status_code}).\n"
                f"    First part of the response: {raw.text[:300]!r}"
            ) from exc

        b64_data = parsed.data[0].b64_json
        return base64.b64decode(b64_data)

    def _friendly_status_error(self, exc: APIStatusError) -> str:
        if exc.status_code == 404:
            return (
                f"Model '{self.model}' isn't available yet.\n"
                f"    Pull it first with: ollama pull {self.model}"
            )
        return f"Ollama returned an error ({exc.status_code}): {exc.message}"


def save_image(image_bytes: bytes, filename: str, output_dir: str = config.OUTPUT_DIR) -> Path:
    """Save image bytes to `output_dir` as `<filename>.png`.

    `filename` is expected to already be filesystem-safe (see `cli.slugify`,
    e.g. the `date_team1_team2` slug for a match).
    """
    out_dir = Path(output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"{filename}.png"
    out_path.write_bytes(image_bytes)
    return out_path