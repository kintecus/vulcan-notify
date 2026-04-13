"""Shared text utilities."""

from __future__ import annotations

import re


def strip_html(html: str) -> str:
    """Minimal HTML-to-text: strip tags, decode common entities, collapse whitespace."""
    text = re.sub(r"<br\s*/?>", "\n", html)
    text = re.sub(r"<[^>]+>", "", text)
    text = text.replace("&nbsp;", " ").replace("&amp;", "&")
    text = text.replace("&lt;", "<").replace("&gt;", ">")
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()
