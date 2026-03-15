"""Tests for AI summarizer."""

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

from vulcan_notify.config import Settings
from vulcan_notify.summarizer import _strip_ansi, summarize

_PATCH_TARGET = "openai.AsyncOpenAI"


def _settings(tmp_path: Path, *, api_key: str | None = "test-key") -> Settings:
    """Create Settings with a temp prompts file."""
    prompts = tmp_path / "prompts.toml"
    prompts.write_text(
        '[default]\n'
        'system = "You summarize school updates."\n'
        'prompt = "Summarize: {sync_output}"\n'
        '\n'
        '[weekly]\n'
        'system = "You write weekly reports."\n'
        'prompt = "Weekly report: {sync_output}"\n'
    )
    return Settings(
        llm_api_key=api_key,
        prompts_file=prompts,
        db_path=tmp_path / "test.db",
        session_file=tmp_path / "session.json",
    )


async def test_returns_none_when_no_api_key(tmp_path: Path) -> None:
    s = _settings(tmp_path, api_key=None)
    result = await summarize("some output", s)
    assert result is None


@patch(_PATCH_TARGET)
async def test_calls_openai_with_correct_params(
    mock_openai_cls: MagicMock,
    tmp_path: Path,
) -> None:
    mock_client = AsyncMock()
    mock_openai_cls.return_value = mock_client
    mock_response = MagicMock()
    mock_response.choices = [MagicMock(message=MagicMock(content="Summary here"))]
    mock_client.chat.completions.create.return_value = mock_response

    s = _settings(tmp_path)
    result = await summarize("Grade: Math 5", s)

    assert result == "Summary here"
    mock_openai_cls.assert_called_once_with(
        base_url="https://api.cerebras.ai/v1",
        api_key="test-key",
    )
    call_kwargs = mock_client.chat.completions.create.call_args.kwargs
    assert call_kwargs["model"] == "llama3.1-8b"
    messages = call_kwargs["messages"]
    assert messages[0]["role"] == "system"
    assert messages[0]["content"] == "You summarize school updates."
    assert messages[1]["role"] == "user"
    assert "Grade: Math 5" in messages[1]["content"]


@patch(_PATCH_TARGET)
async def test_strips_ansi_before_sending(
    mock_openai_cls: MagicMock,
    tmp_path: Path,
) -> None:
    mock_client = AsyncMock()
    mock_openai_cls.return_value = mock_client
    mock_response = MagicMock()
    mock_response.choices = [MagicMock(message=MagicMock(content="Ok"))]
    mock_client.chat.completions.create.return_value = mock_response

    s = _settings(tmp_path)
    ansi_output = "\033[1mBold\033[0m \033[92mGreen\033[0m"
    await summarize(ansi_output, s)

    call_kwargs = mock_client.chat.completions.create.call_args.kwargs
    user_content = call_kwargs["messages"][1]["content"]
    assert "\033[" not in user_content
    assert "Bold" in user_content
    assert "Green" in user_content


@patch(_PATCH_TARGET)
async def test_returns_none_on_api_error(
    mock_openai_cls: MagicMock,
    tmp_path: Path,
) -> None:
    mock_client = AsyncMock()
    mock_openai_cls.return_value = mock_client
    mock_client.chat.completions.create.side_effect = RuntimeError("API down")

    s = _settings(tmp_path)
    result = await summarize("output", s)
    assert result is None


@patch(_PATCH_TARGET)
async def test_loads_custom_prompt_profile(
    mock_openai_cls: MagicMock,
    tmp_path: Path,
) -> None:
    mock_client = AsyncMock()
    mock_openai_cls.return_value = mock_client
    mock_response = MagicMock()
    mock_response.choices = [MagicMock(message=MagicMock(content="Weekly"))]
    mock_client.chat.completions.create.return_value = mock_response

    s = _settings(tmp_path)
    result = await summarize("data", s, profile="weekly")

    assert result == "Weekly"
    call_kwargs = mock_client.chat.completions.create.call_args.kwargs
    assert call_kwargs["messages"][0]["content"] == "You write weekly reports."
    assert "Weekly report:" in call_kwargs["messages"][1]["content"]


def test_strip_ansi() -> None:
    assert _strip_ansi("\033[1mBold\033[0m") == "Bold"
    assert _strip_ansi("\033[92mGreen\033[0m text") == "Green text"
    assert _strip_ansi("plain text") == "plain text"
