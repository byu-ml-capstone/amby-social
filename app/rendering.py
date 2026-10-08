import bleach
from markdown_it import MarkdownIt

ALLOWED_TAGS = [
    "p",
    "br",
    "strong",
    "em",
    "s",  # ~~strikethrough~~
    "code",
    "pre",
    "blockquote",
    "ul",
    "ol",
    "li",
    "h1",
    "h2",
    "h3",
    "h4",
    "h5",
    "h6",
    "a",
    "hr",
    "table",  # GFM tables
    "thead",
    "tbody",
    "tr",
    "th",
    "td",
]
ALLOWED_ATTRIBUTES = {"a": ["href", "title"]}
ALLOWED_PROTOCOLS = ["http", "https", "mailto"]

_md = MarkdownIt("commonmark", {"html": False, "linkify": False}).enable(["table", "strikethrough"])


def render_markdown(text: str | None) -> str:
    if not text:
        return ""
    # Two nets under different holes, not one: html=False above neutralizes
    # raw HTML in the source, while bleach here constrains tags markdown-it
    # itself generates (e.g. `![alt](x)` -> a live <img>). Removing either
    # leaves a real gap — see test_removing_bleach_would_leak_a_live_tag.
    return bleach.clean(
        _md.render(text),
        tags=ALLOWED_TAGS,
        attributes=ALLOWED_ATTRIBUTES,
        protocols=ALLOWED_PROTOCOLS,
        strip=False,
    )
