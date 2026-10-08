from app.rendering import ALLOWED_ATTRIBUTES, ALLOWED_PROTOCOLS, ALLOWED_TAGS, _md, render_markdown


def test_basic_markdown_renders():
    html = render_markdown("**bold** and `code`")
    assert "<strong>bold</strong>" in html
    assert "<code>code</code>" in html



def test_tables_and_strikethrough_render():
    html = render_markdown("| a | b |\n|---|---|\n| 1 | 2 |\n\n~~gone~~")
    assert "<table>" in html
    assert "<th>a</th>" in html
    assert "<td>2</td>" in html
    assert "<s>gone</s>" in html

def test_ordinary_markdown_constructs_survive():
    # The board needs paragraphs, emphasis, lists, code spans, fenced code
    # blocks, and links to remain usable. Full-string assert: an allow-list
    # bug that silently drops one of these (e.g. <p>) would go unnoticed by
    # a substring check but not by this.
    html = render_markdown(
        "# Heading\n\n"
        "A paragraph with *em* text.\n\n"
        "- item one\n"
        "- item two\n\n"
        "```\ncode block\n```\n\n"
        "[a link](https://example.com)"
    )
    assert html == (
        "<h1>Heading</h1>\n"
        "<p>A paragraph with <em>em</em> text.</p>\n"
        "<ul>\n<li>item one</li>\n<li>item two</li>\n</ul>\n"
        "<pre><code>code block\n</code></pre>\n"
        '<p><a href="https://example.com">a link</a></p>\n'
    )


def test_script_tag_in_source_is_neutralized():
    # markdown-it's html=False escapes raw HTML in the Markdown source
    # itself, so the tag never exists as a live element by the time bleach
    # runs. Full-string assert proves the *entire* tag became inert text
    # (not just that some substring got mangled).
    html = render_markdown("<script>alert('xss')</script>")
    assert html == "<p>&lt;script&gt;alert('xss')&lt;/script&gt;</p>\n"
    assert "<script>" not in html.lower()
    assert "alert" in html  # visible as inert text, not executed


def test_img_onerror_in_source_is_neutralized():
    html = render_markdown('<img src=x onerror="alert(1)">')
    assert html == "<p>&lt;img src=x onerror=&quot;alert(1)&quot;&gt;</p>\n"
    assert "<img" not in html.lower()


def test_javascript_url_in_markdown_link_never_becomes_a_link():
    # markdown-it's own link-destination validation refuses to turn this
    # into an <a> at all -- it falls back to the literal source text, so
    # there is no href for a javascript: URL to hide inside.
    html = render_markdown("[click](javascript:alert(1))")
    assert html == "<p>[click](javascript:alert(1))</p>\n"
    assert "<a" not in html.lower()
    assert "click" in html


def test_onclick_and_target_on_raw_anchor_neutralized():
    # An onclick/target pair smuggled in as raw HTML in the source: the
    # whole tag is escaped to text by markdown-it before bleach ever runs.
    html = render_markdown(
        '<a href="https://example.com" onclick="alert(1)" target="_blank">link</a>'
    )
    # the attacker's literal text is visible but inert (entity-encoded),
    # not present as a live, executable tag or attribute.
    assert html == (
        "<p>&lt;a href=&quot;https://example.com&quot; onclick=&quot;alert(1)&quot;"
        " target=&quot;_blank&quot;&gt;link&lt;/a&gt;</p>\n"
    )
    assert "<a " not in html.lower() and "<a>" not in html.lower()


def test_markdown_image_syntax_is_stripped_by_bleach():
    # img is not on the allow-list. Unlike raw HTML, ![alt](src) is
    # legitimate CommonMark syntax that markdown-it happily turns into a
    # real <img> element -- markdown-it's html=False does not touch it,
    # because it isn't "raw HTML in the source". Bleach is the only thing
    # standing between this ordinary-looking Markdown and a live <img> tag
    # (a classic onerror vector), so this proves bleach does real,
    # independent work in the pipeline rather than being redundant with
    # markdown-it's html=False.
    raw = _md.render("![alt](x.png)")
    assert raw == '<p><img src="x.png" alt="alt" /></p>\n'  # markdown-it: a live tag
    sanitized = render_markdown("![alt](x.png)")
    assert "<img" not in sanitized.lower()
    assert sanitized == '<p>&lt;img src="x.png" alt="alt" /&gt;</p>\n'


def test_none_and_empty_are_safe():
    assert render_markdown(None) == ""
    assert render_markdown("") == ""


def test_markdown_it_html_is_actually_disabled():
    # Verify layer one directly rather than assuming the default: feed
    # raw HTML straight to the configured _md instance, bypassing bleach
    # entirely. If html were True (or the default), this <script> would
    # come out untouched.
    raw = _md.render("<script>alert(1)</script>")
    assert raw == "<p>&lt;script&gt;alert(1)&lt;/script&gt;</p>\n"
    assert "<script>" not in raw


def test_bleach_layer_independently_strips_dangerous_html():
    # Feed HTML straight to bleach.clean with this module's allow-list,
    # bypassing markdown-it entirely, so this can't be passing only
    # because markdown-it already neutralized everything upstream.
    def clean(s: str) -> str:
        import bleach

        return bleach.clean(
            s,
            tags=ALLOWED_TAGS,
            attributes=ALLOWED_ATTRIBUTES,
            protocols=ALLOWED_PROTOCOLS,
            strip=False,
        )

    # disallowed tag -> escaped to inert text
    assert clean("<script>alert(1)</script>") == "&lt;script&gt;alert(1)&lt;/script&gt;"
    assert clean('<img src=x onerror="alert(1)">') == '&lt;img src=x onerror="alert(1)"&gt;'

    # allowed tag, disallowed attributes -> tag kept, attributes dropped
    assert (
        clean('<a href="https://example.com" onclick="alert(1)" target="_blank">x</a>')
        == '<a href="https://example.com">x</a>'
    )

    # allowed tag, disallowed protocol -> href itself is dropped
    assert clean('<a href="javascript:alert(1)">x</a>') == "<a>x</a>"


def test_removing_bleach_would_leak_a_live_tag():
    # The load-bearing check: without bleach, render_markdown would just be
    # _md.render(text), and this ordinary-looking Markdown image would
    # reach the page as a live, unescaped <img> element (a real onerror
    # vector) instead of inert text.
    would_be_output_without_bleach = _md.render("![alt](x.png)")
    assert "<img" in would_be_output_without_bleach.lower()  # unsafe if this were the output
    assert "<img" not in render_markdown("![alt](x.png)").lower()  # bleach fixes it
