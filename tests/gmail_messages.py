import base64


def b64(text: str, charset: str = "utf-8") -> str:
    """Gmail API body encoding: unpadded base64url."""
    return base64.urlsafe_b64encode(text.encode(charset)).decode().rstrip("=")


def gmail_message(
    message_id: str = "msg-1",
    *,
    thread_id: str | None = None,
    labels: tuple[str, ...] = ("Label_1",),
    subject: str = "Need a flyer",
    sender: str = "Jane Doe <jane@example.com>",
    body: str = "Please make a flyer for the fall event.",
    payload: dict | None = None,
) -> dict:
    """A `messages.get(format=full)` resource. Gmail thread ids equal the first message id."""
    return {
        "id": message_id,
        "threadId": thread_id or message_id,
        "labelIds": list(labels),
        "snippet": body[:100],
        "payload": payload
        or {
            "mimeType": "text/plain",
            "headers": [
                {"name": "Subject", "value": subject},
                {"name": "From", "value": sender},
            ],
            "body": {"data": b64(body)},
        },
    }


def attachment_part(
    attachment_id: str,
    filename: str,
    size: int = 1234,
    mime_type: str = "application/pdf",
    *,
    content_id: str | None = None,
) -> dict:
    disposition = "inline" if content_id else "attachment"
    headers = [{"name": "Content-Disposition", "value": f'{disposition}; filename="{filename}"'}]
    if content_id:
        headers.append({"name": "Content-ID", "value": f"<{content_id}>"})
    return {
        "mimeType": mime_type,
        "filename": filename,
        "headers": headers,
        "body": {"attachmentId": attachment_id, "size": size},
    }


def with_attachments(*parts: dict, body: str = "See attached") -> dict:
    """A multipart/mixed payload: a plain-text body followed by `parts`."""
    return {
        "mimeType": "multipart/mixed",
        "headers": [
            {"name": "Subject", "value": "Need a flyer"},
            {"name": "From", "value": "Jane Doe <jane@example.com>"},
        ],
        "parts": [{"mimeType": "text/plain", "body": {"data": b64(body)}}, *parts],
    }
