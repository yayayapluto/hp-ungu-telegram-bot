"""Text formatting helpers for Telegram HTML output."""
from __future__ import annotations

import html
import time
from typing import Iterable, Sequence


def escape_html(text: object) -> str:
    return html.escape(str(text), quote=False)


def pre(text: object, language: str = "") -> str:
    """Wrap *text* in an HTML <pre> block, safely escaped."""
    cls = f' class="language-{escape_html(language)}"' if language else ""
    return f"<pre><code{cls}>{escape_html(text)}</code></pre>"


def code(text: object) -> str:
    return f"<code>{escape_html(text)}</code>"


def bold(text: object) -> str:
    return f"<b>{escape_html(text)}</b>"


def human_bytes(value: float | int | None) -> str:
    if value is None:
        return "Unavailable"
    size = float(value)
    negative = size < 0
    size = abs(size)
    for unit in ("B", "KiB", "MiB", "GiB", "TiB"):
        if size < 1024.0 or unit == "TiB":
            if unit == "B":
                rendered = f"{int(size)} {unit}"
            else:
                rendered = f"{size:.1f} {unit}"
            return f"-{rendered}" if negative else rendered
        size /= 1024.0
    return f"{size:.1f} PiB"


def human_duration(seconds: float | int | None) -> str:
    if seconds is None:
        return "Unavailable"
    total = int(max(0, seconds))
    days, rem = divmod(total, 86400)
    hours, rem = divmod(rem, 3600)
    minutes, secs = divmod(rem, 60)
    if days:
        return f"{days}d {hours}h {minutes}m"
    if hours:
        return f"{hours}h {minutes}m {secs}s"
    if minutes:
        return f"{minutes}m {secs}s"
    return f"{secs}s"


def truncate(text: str, limit: int, marker: str = "\n… [truncated]") -> str:
    if len(text) <= limit:
        return text
    return text[: max(0, limit - len(marker))] + marker


def progress_bar(percent: float, width: int = 12) -> str:
    pct = max(0.0, min(100.0, float(percent)))
    filled = int(round(width * pct / 100.0))
    return "[" + "#" * filled + "." * (width - filled) + f"] {pct:5.1f}%"


def kv_table(rows: Iterable[tuple[str, object]], indent: str = "") -> str:
    lines = []
    for key, value in rows:
        lines.append(f"{indent}{escape_html(key)}: {escape_html(value)}")
    return "\n".join(lines)


def utc_timestamp() -> str:
    return time.strftime("%Y-%m-%d %H:%M:%S UTC", time.gmtime())


def shorten(text: str, limit: int) -> str:
    text = text.replace("\n", " ").strip()
    if len(text) <= limit:
        return text
    return text[: limit - 1] + "…"


def bullet_list(items: Sequence[str], empty: str = "None") -> str:
    if not items:
        return empty
    return "\n".join(f"• {item}" for item in items)
