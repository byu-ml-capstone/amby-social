# Ticket Attachments Design

## Goal

Let people attach files to tickets by drag and drop, paste, or a file picker, and have Gmail intake bring each mail's attachments into the ticket it creates. Images preview on the ticket and can be read by LLM clients through MCP.

## Decisions

- **Any file type.** Images get a thumbnail. Every other file is a download link. Marketing mail often carries PDF briefs and design files, so images-only would miss the useful part.
- **Server disk storage**, next to the SQLite database. S3 is not used for now; storage sits behind three functions so it can be swapped later.
- **One Attachments section per ticket**, below the Details description and above the field grid (Type, Priority, …). Thumbnails have a fixed size and wrap, so a long list never squeezes the layout around it.
- **Attach after creation only**, from the ticket detail page. The create modal does not accept files; that would need temporary storage before a ticket exists.
- **Gmail: real attachments only.** Inline parts such as signature logos are skipped.
- **Images are downscaled on arrival and the original is not kept.** This saves disk and caps MCP token cost. Non-image files (PDF, PSD, AI, ZIP) are stored unchanged, so a high-resolution original can still be attached as one of those.

## Data Model

New table `ticket_attachment`:

`id`, `ticket_id` (FK, index), `project_id` (FK, index), `filename` (original name, display only, max 255), `content_type`, `size_bytes` (stored size, after downscaling), `is_image`, `source` (`UPLOAD` / `GMAIL`, uppercase like every other enum in `app/models.py`), `uploaded_by` (FK user), `created_at`.

- Gmail attachments are recorded as uploaded by the user who connected Gmail.
- `delete_ticket_record` and `delete_project` delete attachment rows in the same transaction. Files are removed after the commit. If a file removal fails, the result is an orphan file on disk, never a row pointing at a missing file.

## Storage (`app/attachments.py`)

- Path: `{ATTACHMENTS_DIR}/{project_id}/{attachment_id}`. The user's filename never reaches the filesystem.
- New setting `ATTACHMENTS_DIR`: default `data/attachments`, `/data/attachments` in `docker-compose.yml` (the same volume as the database).
- Functions: `save(project_id, attachment_id, data)`, `path(project_id, attachment_id)` for `FileResponse` streaming, `delete(project_id, attachment_id)`. Nothing else touches the directory.
- Limits: **10 MB per incoming file**, checked before processing, and **1 GB of stored bytes per project**, checked with `SUM(size_bytes)`. There is no counter column.
- Nginx `client_max_body_size` goes from `2m` to `25m` so that several files fit in one upload.

## Image Handling

- **Detection** uses magic bytes, never the extension or the browser's content type. Only PNG, JPEG, GIF, and WebP count as images. SVG is a plain file, because it can carry script.
- **Downscaling** uses Pillow, a new dependency.
  - PNG, JPEG, and WebP whose long edge is over **2000 px** are resized to 2000 px.
  - The format is kept. JPEG is re-encoded at quality 85; PNG is saved with `optimize=True`. The filename does not change.
  - EXIF orientation is applied first, then EXIF is dropped, which also removes GPS data.
  - Re-encoding also discards anything smuggled after the image data.
- **GIF is stored unchanged**, since resizing breaks animation.
- **Memory guard.** The target is an EC2 nano with 512 MB of RAM.
  - Images over **25 megapixels** are not decoded. They are stored unchanged as plain files (download only, no thumbnail).
  - JPEG uses `Image.draft()` to decode at reduced scale.
  - `Image.MAX_IMAGE_PIXELS` is set to that cap, and a decompression-bomb error is handled the same way.
  - `# ponytail:` two large uploads decoding at once can still use about 200 MB. Add a semaphore if uploads become concurrent in practice.
- **No thumbnail files.** The downscaled image is shown with CSS `object-fit` and `loading="lazy"`. Add real thumbnails if pages with many images get slow.

## Web UI and Endpoints

Section placement: in `partials/ticket_detail.html`, between the description editor and `ticket-detail-grid`. The section is rendered by its own partial, `partials/ticket_attachments.html`, so htmx can swap just this section.

- **Images:** a grid of fixed-size tiles (about 96 px squares) that wraps. Clicking a tile opens the image in a new tab (`target="_blank" rel="noopener"`).
- **Other files:** a row each with filename, size, and a download link.
- **Gmail files:** a small "from Gmail" marker.
- **Delete:** a button on each item, shown only to users who may delete it.
- **Empty state:** "Drop files here or paste a screenshot."

Ways to add files (all post to the same endpoint):

- **Drop** onto the section. The drop zone is highlighted while dragging.
- **Paste** anywhere on the ticket detail page when the clipboard holds files. Text paste is unchanged. A screenshot pasted into a comment or description textarea is uploaded as an attachment.
- **"Add files" button**, which opens a file input, for keyboard and mobile users.
- "Uploading…" state while a request is in flight. Errors (too large, quota full) appear inside the section.

Endpoints, following the ticket comment routes:

| Method | Path | Notes |
|---|---|---|
| POST | `/projects/{slug}/tickets/{n}/attachments` | multipart, several files; `project_writer`; returns the section partial for htmx, redirects otherwise |
| GET | `/projects/{slug}/attachments/{id}` | project member; 404 if the attachment belongs to another project |
| POST | `/projects/{slug}/tickets/{n}/attachments/{id}/delete` | uploader or project OWNER, same rule as comment delete |

Response headers when serving a file:

- Every file: `X-Content-Type-Options: nosniff` and `Content-Security-Policy: sandbox`.
- Images: the detected content type and `Content-Disposition: inline`.
- Everything else: `application/octet-stream` and `Content-Disposition: attachment`. An uploaded HTML or SVG file therefore never runs in the app's origin.
- Filenames use RFC 5987 (`filename*=UTF-8''…`) so Korean names survive.

A multi-file upload is all or nothing. If any file fails validation, none are saved and the error names the file.

## Gmail Attachments

`gmail_parse.py` stays pure (no network). `ParsedEmail` gains `attachments: list[GmailAttachmentRef(filename, size, attachment_id)]`.

- **Included:** a part that has a filename and a `body.attachmentId`.
- **Skipped as inline:** a part with `Content-Disposition: inline` and a `Content-ID`. This covers signature logos and newsletter images.
- The current `(N attachments not imported)` line is removed.

Sync, per message:

1. Parse the message: title, body, attachment refs.
2. For each ref, skip it without downloading when its size is over 10 MB or it would push the project over 1 GB. Otherwise fetch it with `messages.attachments.get` (new `GmailClient.attachment` method) and run the Image Handling steps.
3. Write the files to disk.
4. Commit the ticket, attachment rows, and delivery rows in one transaction. If the commit fails, delete the files written in step 3.

Skipped files get one line each at the end of the description, for example `report.pdf (32 MB) — not imported, open in Gmail`. The description cap (`EMAIL_BODY_MAX_CHARS`) applies to the body only, so these lines are never cut off.

| Situation | Behavior |
|---|---|
| Attachment fetch 5xx / 429 / network | Propagates like any transient error. Nothing is committed, files already written for this message are deleted, and `historyId` is held. The next cycle replays the message, and dedupe prevents a second ticket. |
| Attachment fetch 404 | Skip that file with a "not imported" line and still create the ticket |
| Too large / quota full | Skip that file with a "not imported" line |
| Image decode fails | Store the file unchanged as a plain file |

## API and MCP

- `TicketOut` (`app/schemas.py`) gains `attachments: [{id, filename, content_type, size_bytes, is_image, source}]`, so the MCP `get_ticket` tool lists them.
- New API route `GET /api/v1/attachments/{id}`, gated by API token and project membership. It returns the file bytes.
- New MCP tool `get_attachment(attachment_id)`:
  - **For images:** returns an MCP image content block, downscaled to a long edge of **1568 px**. That is the size the Claude API resizes to anyway, so anything larger only costs tokens. The result is roughly 1–2k tokens per image.
  - **For other files:** returns only the metadata and a note that the content is not readable through MCP.

## Security

- Files are served only through an authenticated route that checks project membership. There is no public static path.
- No filename reaches the filesystem. Image detection uses magic bytes, and non-images always download.
- Images are re-encoded, which strips EXIF and anything trailing the image data.
- Mail attachments and uploaded images are untrusted. Text inside an image read through MCP is a prompt-injection surface for LLM clients, the same class as the email body. This is noted, not mitigated further.
- There is no virus scanning. The server never executes or renders non-image files, but the risk stays with whoever opens a downloaded file. ClamAV is the upgrade path if needed.

## Testing

- **Storage:** save, read, and delete round trip. A filename containing `../` has no effect on the stored path.
- **Detection:** real PNG, JPEG, GIF, and WebP are images. HTML renamed to `.png` and SVG are plain files.
- **Downscaling:**
  - A 3000 px image comes back at 2000 px in the same format with EXIF gone.
  - A small image is stored at its original dimensions.
  - GIF is unchanged.
  - An over-cap image is stored unchanged as a plain file.
  - A corrupt image is stored as a plain file.
- **Upload route:**
  - several files in one request are all saved;
  - over 10 MB is rejected, and a failed multi-file upload saves nothing;
  - a full project quota is rejected;
  - a non-member gets 403;
  - the response is the htmx partial.
- **Serve route:**
  - images get `inline` with the detected type;
  - other files get `attachment` with `octet-stream`;
  - `nosniff` and `sandbox` are always set;
  - a Korean filename is encoded;
  - another project's attachment id gives 404.
- **Delete:** the uploader and an OWNER can delete; another member gets 403. Deleting a ticket or project removes the rows and files.
- **Gmail parser:** real attachments are listed and inline `Content-ID` parts are not.
- **Gmail sync** (fake client):
  - attachments land on the ticket;
  - an oversize file gets a "not imported" line and no download call;
  - a 5xx on an attachment commits nothing and holds `historyId`, and the replay creates exactly one ticket;
  - a 404 skips just that file;
  - a failed commit removes the files written for that message.
- **API and MCP:** `TicketOut` lists attachments; `get_attachment` returns an image block of at most 1568 px, and metadata for non-images.
- **Migration:** the new table is added to the migration test.
- **Browser:** drop, paste, and new-tab open are JavaScript, so they are verified once with Playwright on desktop and mobile, not with pytest.

## Deployment Changes

- `deploy/nginx/kanbanflow.conf`: `client_max_body_size 25m`.
- `docker-compose.yml`: `ATTACHMENTS_DIR=/data/attachments`.
- `.env.example` and README: document `ATTACHMENTS_DIR` and the limits.
- `pyproject.toml`: add `pillow`.

## Implementation Notes

Decided while implementing (see the plan for detail):

- `create_ticket` takes `attachment_rows`. SQLAlchemy does not order inserts by foreign key without a `relationship()`, and SQLite checks foreign keys per insert, so `create_ticket` flushes the ticket first and then adds the Gmail attachment rows in the same commit.
- Only `get_ticket` returns attachments, through `TicketDetailOut`. `TicketOut` and `list_tickets` are unchanged, so listing tickets costs no extra query per ticket.
- Upload and delete are JavaScript-driven and always return the section partial. The section sits inside the ticket form, and HTML cannot nest forms.
- The security-header middleware sets CSP with `setdefault`, so an attachment response keeps its `sandbox` policy.
- Attachment pick and delete clicks go through the existing delegated document click handler in `app.js`.
- On pointer devices, delete buttons appear on hover or focus; touch screens always show them. Thumbnails use an `auto-fill` grid of square tiles.
- The browser uploads one file per request, so every request stays under the proxy's 25 MB body limit. The server is still all-or-nothing per request, and the first rejected file stops the files after it.
- Image decodes run one at a time under a lock, and the image is resized before EXIF rotation. The peak for a 25 MP RGBA PNG dropped from +244 MB to +149 MB. GIFs, which are stored unchanged, get the same 25 MP cap, and larger ones become downloads.
- Gmail notes and skips an attachment that fails with a 4xx other than 401/429, or whose data does not decode. Only transient errors hold `historyId`.

## Out of Scope

- Inline images in the description (GitHub style).
- Attaching from the create modal.
- Generated thumbnails and S3.
- Renaming attachments and per-comment attachments.
- Reading non-image files through MCP.
- Virus scanning and an orphan-file cleanup job.
- LLM refinement of email tickets into a template, which is designed separately once the template exists.
