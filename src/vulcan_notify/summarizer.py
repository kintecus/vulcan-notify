"""AI-powered summary of sync results using OpenAI-compatible API."""

from __future__ import annotations

import logging
import re
import tomllib
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from vulcan_notify.config import Settings

log = logging.getLogger(__name__)

_ANSI_RE = re.compile(r"\033\[[0-9;]*m")


def _strip_ansi(text: str) -> str:
    return _ANSI_RE.sub("", text)


async def summarize(
    sync_output: str,
    settings: Settings,
    profile: str = "default",
) -> str | None:
    """Generate an AI summary of sync output.

    Returns None if LLM is not configured or on any error.
    """
    if not settings.llm_api_key:
        return None

    try:
        from openai import AsyncOpenAI

        prompts = _load_prompts(settings, profile)
        if prompts is None:
            return None

        system_prompt, user_prompt_template = prompts
        clean_output = _strip_ansi(sync_output)
        user_prompt = user_prompt_template.format(sync_output=clean_output)

        client = AsyncOpenAI(
            base_url=settings.llm_base_url,
            api_key=settings.llm_api_key,
        )
        response = await client.chat.completions.create(
            model=settings.llm_model,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
        )
        content = response.choices[0].message.content
        return content if content else None

    except Exception:
        log.exception("Failed to generate AI summary")
        return None


def _load_prompts(
    settings: Settings,
    profile: str,
) -> tuple[str, str] | None:
    """Load system and user prompts from TOML file."""
    try:
        with open(settings.prompts_file, "rb") as f:
            data = tomllib.load(f)
        section = data.get(profile)
        if not section:
            log.warning("Prompt profile '%s' not found in %s", profile, settings.prompts_file)
            return None
        return section["system"], section["prompt"]
    except (FileNotFoundError, KeyError, tomllib.TOMLDecodeError) as e:
        log.warning("Failed to load prompts from %s: %s", settings.prompts_file, e)
        return None
