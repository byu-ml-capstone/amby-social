from app.gmail_parse import EMAIL_BODY_MAX_CHARS, GmailAttachmentRef, parse_message
from tests.gmail_messages import attachment_part, b64, gmail_message, with_attachments


def _headers(subject="Need a flyer", sender="Jane Doe <jane@example.com>", *extra):
    return [{"name": "Subject", "value": subject}, {"name": "From", "value": sender}, *extra]


def test_plain_text_message_becomes_title_body_and_sender():
    parsed = parse_message(
        gmail_message(
            subject="Re: Fwd: Need a flyer",
            body="Please make a flyer.\r\n\r\n\r\n\r\nThanks!",
        )
    )
    assert parsed.title == "Need a flyer"
    assert parsed.sender == "Jane Doe <jane@example.com>"
    assert parsed.body == "Please make a flyer.\n\nThanks!"
    assert parsed.attachments == ()
    assert parsed.description == (
        "From: Jane Doe <jane@example.com>\n\nPlease make a flyer.\n\nThanks!"
    )


def test_multipart_prefers_plain_text_over_html():
    payload = {
        "mimeType": "multipart/alternative",
        "headers": _headers(),
        "parts": [
            {"mimeType": "text/html", "body": {"data": b64("<p>HTML version</p>")}},
            {"mimeType": "text/plain", "body": {"data": b64("Plain version")}},
        ],
    }
    assert parse_message(gmail_message(payload=payload)).body == "Plain version"


def test_html_only_message_drops_style_script_and_markup():
    markup = (
        "<html><head><style>.hero { color: red; }</style></head><body>"
        "<script>track()</script><h1>Fall event</h1><p>Need&nbsp;a <b>flyer</b></p>"
        "<br>Thanks</body></html>"
    )
    payload = {"mimeType": "text/html", "headers": _headers(), "body": {"data": b64(markup)}}
    assert parse_message(gmail_message(payload=payload)).body == (
        "Fall event\n\nNeed a flyer\n\nThanks"
    )


def test_quoted_plain_text_reply_is_removed():
    body = (
        "Can we move it to Friday?\n\n"
        "On Mon, Sep 28, 2026 at 9:00 AM Jane Doe <jane@example.com>\nwrote:\n"
        "> Original request\n> more"
    )
    assert parse_message(gmail_message(body=body)).body == "Can we move it to Friday?"


def test_quoted_html_reply_is_removed():
    markup = (
        "<div>Sounds good</div><div class='gmail_quote'>"
        "<div>On Mon, Sep 28, 2026 Jane wrote:</div><blockquote>Old text</blockquote></div>"
    )
    payload = {"mimeType": "text/html", "headers": _headers(), "body": {"data": b64(markup)}}
    assert parse_message(gmail_message(payload=payload)).body == "Sounds good"


def test_body_that_is_only_a_quote_falls_back_to_raw_text():
    assert parse_message(gmail_message(body="> note only")).body == "> note only"


def test_message_without_any_body_uses_the_snippet():
    message = gmail_message(payload={"mimeType": "multipart/mixed", "headers": _headers()})
    message["snippet"] = "Tom &amp; Jerry"
    assert parse_message(message).body == "Tom & Jerry"


def test_empty_subject_uses_sender():
    assert parse_message(gmail_message(subject="  Re:  ")).title == (
        "(no subject) from Jane Doe <jane@example.com>"
    )


def test_long_subject_is_cut_to_title_limit():
    assert len(parse_message(gmail_message(subject="x" * 400)).title) == 255


def test_long_body_is_capped():
    parsed = parse_message(gmail_message(body="x" * (EMAIL_BODY_MAX_CHARS + 500)))
    assert parsed.body == "x" * EMAIL_BODY_MAX_CHARS + "\n\n…(truncated, 500 chars omitted)"


def test_real_attachments_are_listed_and_inline_images_skipped():
    payload = with_attachments(
        attachment_part("att-1", "brief.pdf", 1234),
        attachment_part("att-2", "logo.png", 99, "image/png", content_id="logo@sig"),
        attachment_part("att-3", "photo.jpg", 5000, "image/jpeg"),
    )
    parsed = parse_message(gmail_message(payload=payload))
    assert parsed.attachments == (
        GmailAttachmentRef("brief.pdf", "application/pdf", 1234, "att-1"),
        GmailAttachmentRef("photo.jpg", "image/jpeg", 5000, "att-3"),
    )
    assert parsed.body == "See attached"
    assert parsed.description == "From: Jane Doe <jane@example.com>\n\nSee attached"


def test_declared_charset_is_respected():
    content_type = {"name": "Content-Type", "value": 'text/plain; charset="iso-8859-1"'}
    payload = {
        "mimeType": "text/plain",
        "headers": _headers("Menu", "Chef <chef@example.com>", content_type),
        "body": {"data": b64("Café menu", "iso-8859-1")},
    }
    assert parse_message(gmail_message(payload=payload)).body == "Café menu"


def test_unknown_charset_falls_back_to_utf8():
    content_type = {"name": "Content-Type", "value": "text/plain; charset=x-unknown"}
    payload = {
        "mimeType": "text/plain",
        "headers": _headers("Hi", "A <a@example.com>", content_type),
        "body": {"data": b64("hello")},
    }
    assert parse_message(gmail_message(payload=payload)).body == "hello"
