"""Template filters for VM manager views."""

import re
from html.parser import HTMLParser

from django import template
from django.utils.safestring import mark_safe

register = template.Library()

# Tags and attributes allowed in VM descriptions.
# Community scripts set HTML descriptions via qm set -description; these
# are the elements they actually use.
_ALLOWED_TAGS = frozenset({
    "div", "span", "p", "a", "img", "h1", "h2", "h3", "h4",
    "br", "hr", "b", "strong", "i", "em",
})

_ALLOWED_ATTRS = frozenset({
    "href", "src", "alt", "style", "target", "rel", "class", "align",
})


class _Sanitiser(HTMLParser):
    """Strip HTML to a safe subset."""

    def __init__(self):
        super().__init__()
        self.parts = []
        self._skip_depth = 0

    def handle_starttag(self, tag, attrs):
        tag = tag.lower()
        if tag not in _ALLOWED_TAGS:
            self._skip_depth += 1
            return
        safe_attrs = []
        for name, value in attrs:
            name = name.lower()
            if name not in _ALLOWED_ATTRS:
                continue
            if name == "href" and value and not re.match(r"^https?://", value):
                continue
            if name == "src" and value and not re.match(r"^https?://", value):
                continue
            if name == "target":
                value = "_blank"
            safe_attrs.append(f'{name}="{_escape_attr(value or "")}"')
        attr_str = (" " + " ".join(safe_attrs)) if safe_attrs else ""
        self.parts.append(f"<{tag}{attr_str}>")

    def handle_endtag(self, tag):
        tag = tag.lower()
        if tag not in _ALLOWED_TAGS:
            if self._skip_depth > 0:
                self._skip_depth -= 1
            return
        self.parts.append(f"</{tag}>")

    def handle_data(self, data):
        if self._skip_depth > 0:
            return
        self.parts.append(_escape_data(data))

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)

    def get_output(self):
        return "".join(self.parts)


def _escape_attr(value):
    return value.replace("&", "&amp;").replace('"', "&quot;").replace("<", "&lt;").replace(">", "&gt;")


def _escape_data(value):
    return value.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


@register.filter(name="safe_html_description")
def safe_html_description(value):
    """Render a VM description as sanitised HTML.

    If the value contains HTML tags, sanitise to a safe subset.
    Otherwise return it escaped as-is.
    """
    if not value or not isinstance(value, str):
        return value or ""

    # Quick check: if no HTML tags, just return escaped text
    if "<" not in value:
        return value

    sanitiser = _Sanitiser()
    sanitiser.feed(value)
    return mark_safe(sanitiser.get_output())
