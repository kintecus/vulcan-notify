"""Tests for the notification delivery module."""

from unittest.mock import AsyncMock, MagicMock, patch

from vulcan_notify.notifier import send_notification


def _make_mock_session(status: int = 200) -> MagicMock:
    """Create a properly nested async context manager mock for aiohttp."""
    mock_response = MagicMock()
    mock_response.status = status
    mock_response.text = AsyncMock(return_value="ok")

    # session.post() returns an async context manager
    mock_post_cm = MagicMock()
    mock_post_cm.__aenter__ = AsyncMock(return_value=mock_response)
    mock_post_cm.__aexit__ = AsyncMock(return_value=False)

    mock_session = MagicMock()
    mock_session.post = MagicMock(return_value=mock_post_cm)
    mock_session.__aenter__ = AsyncMock(return_value=mock_session)
    mock_session.__aexit__ = AsyncMock(return_value=False)

    return mock_session


async def test_send_notification_success() -> None:
    mock_session = _make_mock_session(200)

    with patch("vulcan_notify.notifier.aiohttp.ClientSession", return_value=mock_session):
        result = await send_notification(title="Test", message="Hello")

    assert result is True


async def test_send_notification_with_tags() -> None:
    mock_session = _make_mock_session(200)

    with patch("vulcan_notify.notifier.aiohttp.ClientSession", return_value=mock_session):
        result = await send_notification(
            title="Grade",
            message="New grade",
            priority=4,
            tags=["school", "pencil"],
        )

    assert result is True
    call_kwargs = mock_session.post.call_args
    headers = call_kwargs.kwargs.get("headers", {})
    assert headers["Tags"] == "school,pencil"
    assert headers["Priority"] == "4"


async def test_send_notification_failure() -> None:
    mock_session = _make_mock_session(500)

    with patch("vulcan_notify.notifier.aiohttp.ClientSession", return_value=mock_session):
        result = await send_notification(title="Test", message="Hello")

    assert result is False
