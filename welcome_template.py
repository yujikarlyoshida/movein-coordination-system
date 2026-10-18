"""
The welcome email, from your own Outlook template -- exactly as Outlook shows it.

Outlook Mail Templates cannot be read by any app, so the template is captured
once from Outlook itself: insert it into a new message in the app's Outlook
tab, and the message body's HTML is saved privately:

    ~/Library/Application Support/MoveInTriage/welcome_template.html   (0600)

It holds your property's details, so it never goes into the repository; this
module contains no template content of its own.

The captured HTML is used as it is. Only these things change:

  * (Resident Name) becomes the resident's first name, or "Maria and James"
  * elements set in the FluentSystemIcons font are removed -- the template
    carries two empty icon blocks that otherwise render as stray boxes
  * icon-font (private-use) characters and emoji are removed
  * Outlook's automatic signature (the block it marks id="Signature") is
    removed. A new message already gets the sender's own signature, so
    keeping it would sign every email twice -- and a shared profile would
    sign a teammate's emails with the capturer's name
  * images that only existed in that one Outlook window (blob: addresses)
    are removed; they would arrive broken

A plain-text copy for the email's text part is derived from the same HTML.
"""

from __future__ import annotations

import html
import os
import re
from html.parser import HTMLParser
from pathlib import Path

_DEFAULT = Path.home() / "Library" / "Application Support" / "MoveInTriage" / "welcome_template.html"
PLACEHOLDER = "(Resident Name)"

# Private-use (icon fonts), emoji, dingbats, variation selector.
_GLYPHS = re.compile("[-\U0001f000-\U0001ffff☀-➿️]")


class _Path:
    """The template's location, read at use: MOVEIN_TEMPLATE_PATH points tests
    at a scratch file, so no test ever reads the real template."""

    def __fspath__(self):
        return os.environ.get("MOVEIN_TEMPLATE_PATH", str(_DEFAULT))

    def __getattr__(self, name):
        return getattr(Path(self.__fspath__()), name)


TEMPLATE_PATH = _Path()


def available() -> bool:
    path = Path(TEMPLATE_PATH)
    return path.exists() and path.stat().st_size > 0


def load() -> str:
    return Path(TEMPLATE_PATH).read_text(encoding="utf-8")


def save(template_html: str) -> None:
    """Keep the captured template privately (owner-only), outside the repository."""
    path = Path(TEMPLATE_PATH)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(path, os.O_CREAT | os.O_WRONLY | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        fh.write(template_html)
    os.chmod(path, 0o600)


def greeting_names(first_names: list[str]) -> str:
    names = [n for n in first_names if n]
    if not names:
        return "there"
    if len(names) == 1:
        return names[0]
    return ", ".join(names[:-1]) + f" and {names[-1]}"


def _is_signature(attrs: dict) -> bool:
    """Outlook's automatic signature block: id "Signature" (or "x_Signature"
    once a message has been replied to)."""
    return (attrs.get("id") or "").lower() in ("signature", "x_signature")


def _is_temporary_image(tag: str, attrs: dict) -> bool:
    return tag == "img" and (attrs.get("src") or "").lower().startswith("blob:")


class _DropIcons(HTMLParser):
    """Rebuilds the HTML, leaving out every element styled FluentSystemIcons,
    Outlook's signature block (each with everything inside it) and temporary
    images. Everything else passes through unchanged."""

    VOID = {"br", "img", "hr", "input", "meta", "link", "col", "area", "base", "wbr", "source"}

    def __init__(self):
        super().__init__(convert_charrefs=False)
        self.out: list[str] = []
        self.skip = 0

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        style = a.get("style") or ""
        if self.skip:
            if tag not in self.VOID:
                self.skip += 1
            return
        if tag not in self.VOID and ("fluentsystemicons" in style.lower().replace(" ", "") or _is_signature(a)):
            self.skip = 1
            return
        if _is_temporary_image(tag, a):
            return
        self.out.append(self.get_starttag_text())

    def handle_startendtag(self, tag, attrs):
        if not self.skip and not _is_temporary_image(tag, dict(attrs)):
            self.out.append(self.get_starttag_text())

    def handle_endtag(self, tag):
        if self.skip:
            if tag not in self.VOID:
                self.skip -= 1
            return
        self.out.append(f"</{tag}>")

    def handle_data(self, data):
        if not self.skip:
            self.out.append(data)

    def handle_entityref(self, name):
        if not self.skip:
            self.out.append(f"&{name};")

    def handle_charref(self, name):
        if not self.skip:
            self.out.append(f"&#{name};")

    def handle_comment(self, data):
        if not self.skip:
            self.out.append(f"<!--{data}-->")


def clean(template_html: str) -> str:
    """The template without icon-font elements, Outlook's signature block,
    temporary images or stray glyphs."""
    p = _DropIcons()
    p.feed(template_html)
    p.close()
    return _GLYPHS.sub("", "".join(p.out))


class _Text(HTMLParser):
    """Plain text from the HTML: line breaks at block ends, "- " for list items."""

    BLOCK = {"p", "div", "br", "tr", "li", "h1", "h2", "h3", "h4", "h5", "h6", "table", "ul", "ol"}

    def __init__(self):
        super().__init__()
        self.parts: list[str] = []

    def handle_starttag(self, tag, attrs):
        if tag == "li":
            self.parts.append("\n- ")
        elif tag in self.BLOCK:
            self.parts.append("\n")

    def handle_endtag(self, tag):
        if tag in self.BLOCK:
            self.parts.append("\n")

    def handle_data(self, data):
        self.parts.append(data)


def to_text(template_html: str) -> str:
    p = _Text()
    p.feed(template_html)
    p.close()
    lines = [" ".join(line.replace(" ", " ").split()) for line in "".join(p.parts).split("\n")]
    text = "\n".join(lines)
    return re.sub(r"\n{3,}", "\n\n", text).strip() + "\n"


def render(first_names: list[str], template: str | None = None) -> tuple[str, str]:
    """The email for these residents as (html, plain text), from the captured template."""
    source = clean(template if template is not None else load())
    filled = source.replace(PLACEHOLDER, html.escape(greeting_names(first_names)))
    return filled, to_text(filled)
