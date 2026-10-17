"""
The welcome email, from your own template.

Your template lives in Outlook as a Mail Template, which no app can read, so a
copy of its text is kept here, privately:

    ~/Library/Application Support/MoveInTriage/welcome_template.txt   (0600)

It holds your property's details (addresses, contact emails), so it never goes
into the repository; this module contains no template content of its own.

Copying a template out of Outlook as text loses its formatting. render() puts it
back from the text's own structure, so the email reads as designed:

    short title lines ("Rent Payment")            -> bold headings
    lines after a sentence ending in ":"          -> a bullet list
    a "Community > Services > ..." path           -> bold
    lines with a P.O. Box or a state + ZIP        -> an address block
    email addresses                               -> mailto links
    two or more blank lines                       -> a section break

(Resident Name) becomes the resident's first name, or "Maria and James" for
roommates. Icon-font glyphs and emoji are stripped: the Outlook template carries
two empty icon blocks that otherwise render as a stray box. Outlook adds your
own signature under "With gratitude," when you compose, so none is added here.
"""

from __future__ import annotations

import html
import os
import re
from pathlib import Path

_DEFAULT = Path.home() / "Library" / "Application Support" / "MoveInTriage" / "welcome_template.txt"


class _Path:
    """The template's location, read at use: MOVEIN_TEMPLATE_PATH points tests
    at a scratch file, so no test ever reads the real template."""

    def __fspath__(self):
        return os.environ.get("MOVEIN_TEMPLATE_PATH", str(_DEFAULT))

    def __getattr__(self, name):
        return getattr(Path(self.__fspath__()), name)


TEMPLATE_PATH = _Path()
PLACEHOLDER = "(Resident Name)"

# Private-use (icon fonts), emoji and dingbats: never part of the message.
_GLYPHS = re.compile("[-\U0001f000-\U0001ffff☀-➿️]")
_ADDRESS = re.compile(r"\b[A-Z]{2}\s+\d{5}(-\d{4})?\b|\bP\.?\s?O\.?\s+Box\b", re.I)
_EMAIL = re.compile(r"[\w.+-]+@[\w-]+(\.[\w-]+)+")

FONT = "font-family: Calibri, Arial, Helvetica, sans-serif; font-size: 15px; line-height: 1.5; color: #1f1f1f"


def available() -> bool:
    path = Path(TEMPLATE_PATH)
    return path.exists() and path.stat().st_size > 0


def load() -> str:
    return Path(TEMPLATE_PATH).read_text(encoding="utf-8")


def save(text: str) -> None:
    """Keep a template privately (owner-only), outside the repository."""
    path = Path(TEMPLATE_PATH)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(path, os.O_CREAT | os.O_WRONLY | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        fh.write(text)
    os.chmod(path, 0o600)


def greeting_names(first_names: list[str]) -> str:
    names = [n for n in first_names if n]
    if not names:
        return "there"
    if len(names) == 1:
        return names[0]
    return ", ".join(names[:-1]) + f" and {names[-1]}"


def _is_heading(line: str) -> bool:
    return (len(line) <= 45 and not re.search(r"[.:,;!?]$", line) and "@" not in line
            and ">" not in line and not line[:1].isdigit() and line[:1].isupper())


def _blocks(text: str) -> list[tuple[str, object]]:
    """The template as (kind, content) blocks: heading, para, list, path, address, break."""
    lines = [_GLYPHS.sub("", raw).replace(" ", " ").rstrip() for raw in text.replace("\r", "").split("\n")]
    out: list[tuple[str, object]] = []
    i, blank_run = 0, 0
    while i < len(lines):
        line = lines[i].strip()
        if not line:
            blank_run += 1
            i += 1
            continue
        if blank_run >= 2 and out:
            out.append(("break", None))
        blank_run = 0

        if line.endswith(":"):
            out.append(("para", line))
            i += 1
            # One blank line may sit between an introduction and what it introduces.
            if i < len(lines) and not lines[i].strip() and i + 1 < len(lines) and lines[i + 1].strip():
                i += 1
            items = []
            while i < len(lines) and lines[i].strip():
                item = lines[i].strip()
                # A long sentence after the items is the next paragraph, not an item.
                if items and len(item) > 90 and item.endswith(".") and not items[-1].endswith("."):
                    break
                items.append(item)
                i += 1
            if not items:
                continue
            if any(_ADDRESS.search(x) for x in items):
                out.append(("address", items))
            elif len(items) == 1 and ">" in items[0]:
                out.append(("path", items[0]))
            elif len(items) == 1:
                out.append(("para", items[0]))
            else:
                out.append(("list", items))
            continue

        if ">" in line and len(line) <= 90:
            out.append(("path", line))
        elif _is_heading(line):
            out.append(("heading", line))
        else:
            out.append(("para", line))
        i += 1
    return out


def _inline(text: str) -> str:
    """Escape, then link email addresses (a trailing full stop stays outside)."""
    escaped = html.escape(text, quote=False)
    return _EMAIL.sub(lambda m: f'<a href="mailto:{m.group(0)}" style="color:#0f6cbd">{m.group(0)}</a>', escaped)


def render(first_names: list[str], template: str | None = None) -> tuple[str, str]:
    """The email for these residents as (html, plain text)."""
    text = (template if template is not None else load()).replace(PLACEHOLDER, greeting_names(first_names))
    parts_html, parts_text = [], []
    for kind, content in _blocks(text):
        if kind == "heading":
            parts_html.append(f'<p style="margin:18px 0 4px"><b style="font-size:16px">{_inline(content)}</b></p>')
            parts_text.append(f"\n{content}")
        elif kind == "para":
            parts_html.append(f'<p style="margin:0 0 10px">{_inline(content)}</p>')
            parts_text.append(content)
        elif kind == "path":
            parts_html.append(f'<p style="margin:0 0 10px"><b>{_inline(content)}</b></p>')
            parts_text.append(content)
        elif kind == "list":
            items = "".join(f'<li style="margin:0 0 4px">{_inline(x)}</li>' for x in content)
            parts_html.append(f'<ul style="margin:0 0 12px 22px;padding:0">{items}</ul>')
            parts_text.append("\n".join(f"  - {x}" for x in content))
        elif kind == "address":
            parts_html.append('<p style="margin:0 0 12px 22px">' + "<br>".join(_inline(x) for x in content) + "</p>")
            parts_text.append("\n".join(f"  {x}" for x in content))
        elif kind == "break":
            parts_html.append('<div style="height:14px"></div>')
            parts_text.append("")
    body_html = f'<div style="{FONT}">' + "".join(parts_html) + "</div>"
    return body_html, "\n".join(parts_text).strip() + "\n"
