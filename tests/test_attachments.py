import io
import os
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest
from PIL import Image

from app import attachments
from tests.images import image_bytes


def _open(data):
    return Image.open(io.BytesIO(data))


def test_storage_round_trip_is_keyed_by_ids():
    attachments.save("project-1", "att-1", b"hello")
    assert attachments.path("project-1", "att-1").read_bytes() == b"hello"
    attachments.delete("project-1", "att-1")
    assert not attachments.path("project-1", "att-1").exists()
    attachments.delete("project-1", "att-1")  # already gone is fine


def test_project_files_are_removed_together():
    attachments.save("project-1", "att-1", b"a")
    attachments.save("project-1", "att-2", b"b")
    attachments.delete_project_files("project-1")
    assert not attachments.path("project-1", "att-1").parent.exists()


def test_uploaded_filename_is_display_only():
    assert attachments.clean_filename("../../etc/passwd") == "passwd"
    assert attachments.clean_filename("C:\\Users\\me\\brief.pdf") == "brief.pdf"
    assert attachments.clean_filename('evil\r\n"name".pdf') == 'evil "name".pdf'
    assert attachments.clean_filename("") == "file"
    assert attachments.clean_filename(None) == "file"
    assert len(attachments.clean_filename("a" * 300 + ".pdf")) == 255


@pytest.mark.parametrize(
    ("fmt", "content_type"),
    [("PNG", "image/png"), ("JPEG", "image/jpeg"), ("GIF", "image/gif"), ("WEBP", "image/webp")],
)
def test_real_images_are_detected_by_their_bytes(fmt, content_type):
    assert attachments.sniff_image(image_bytes(fmt=fmt)) == content_type


def test_markup_is_never_an_image_whatever_its_declared_type():
    html = b"<html><script>alert(1)</script></html>"
    svg = b'<svg xmlns="http://www.w3.org/2000/svg"><script>alert(1)</script></svg>'
    assert attachments.prepare(html, "image/png") == (html, "image/png", False)
    assert attachments.prepare(svg, "image/svg+xml") == (svg, "image/svg+xml", False)
    assert attachments.prepare(b"%PDF-1.4", None) == (
        b"%PDF-1.4",
        "application/octet-stream",
        False,
    )


def test_unsupported_image_format_is_a_download():
    heic = b"\x00\x00\x00\x18ftypheic" + b"\x00" * 16
    assert attachments.prepare(heic, "image/heic") == (heic, "image/heic", False)


def test_large_image_is_downscaled_in_its_own_format_without_exif():
    exif = Image.Exif()
    exif[0x010F] = "Camera maker"
    data = image_bytes((3000, 1500), "JPEG", exif=exif.tobytes())
    stored, content_type, is_image = attachments.prepare(data, "image/jpeg")
    with _open(stored) as image:
        assert (image.format, image.size) == ("JPEG", (2000, 1000))
        assert not image.getexif()
    assert (content_type, is_image) == ("image/jpeg", True)


def test_exif_orientation_is_applied_before_exif_is_dropped():
    exif = Image.Exif()
    exif[0x0112] = 6  # display rotated 90° clockwise
    stored, _, _ = attachments.prepare(image_bytes((300, 100), "JPEG", exif=exif.tobytes()), None)
    with _open(stored) as image:
        assert image.size == (100, 300)


def test_small_png_keeps_its_size():
    stored, content_type, is_image = attachments.prepare(image_bytes((640, 480)), None)
    with _open(stored) as image:
        assert (image.format, image.size) == ("PNG", (640, 480))
    assert (content_type, is_image) == ("image/png", True)


def test_gif_is_stored_unchanged():
    data = image_bytes((3000, 100), "GIF")
    assert attachments.prepare(data, "image/gif") == (data, "image/gif", True)


def test_animated_webp_is_stored_unchanged():
    frames = [Image.new("RGB", (3000, 10), color) for color in ("red", "blue")]
    out = io.BytesIO()
    frames[0].save(out, "WEBP", save_all=True, append_images=frames[1:])
    data = out.getvalue()
    assert attachments.prepare(data, None) == (data, "image/webp", True)


def test_image_over_the_pixel_cap_is_kept_as_a_download(monkeypatch):
    monkeypatch.setattr(attachments, "IMAGE_MAX_PIXELS", 100)
    data = image_bytes((20, 20))
    assert attachments.prepare(data, "image/png") == (data, "application/octet-stream", False)


def test_corrupt_image_is_kept_as_a_download():
    data = image_bytes()[:40]
    assert attachments.prepare(data, "image/png") == (data, "application/octet-stream", False)


_PEAK_MEMORY_PROBE = """
import io, resource, sys
from PIL import Image
from app import attachments
out = io.BytesIO()
Image.new("RGBA", (5000, 5000), (10, 20, 30, 40)).save(out, "PNG")
data = out.getvalue()
scale = 1 if sys.platform == "darwin" else 1024
before = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss * scale
attachments.prepare(data, None)
print((resource.getrusage(resource.RUSAGE_SELF).ru_maxrss * scale - before) // 2**20)
"""


def test_downscaling_a_25_megapixel_png_stays_within_a_nano_budget():
    # The raw RGBA canvas alone is ~95 MB; the box has 512 MB for everything.
    probe = subprocess.run(
        [sys.executable, "-c", _PEAK_MEMORY_PROBE],
        capture_output=True,
        check=True,
        text=True,
        env={**os.environ, "PYTHONPATH": str(Path(__file__).resolve().parents[1])},
    )
    assert int(probe.stdout) < 180, f"prepare() added {probe.stdout.strip()} MB at peak"


def test_image_decodes_run_one_at_a_time(monkeypatch):
    active, peak = 0, 0
    guard = threading.Lock()

    def slow_reencode(data):
        nonlocal active, peak
        with guard:
            active += 1
            peak = max(peak, active)
        time.sleep(0.05)
        with guard:
            active -= 1
        return data

    monkeypatch.setattr(attachments, "_reencode", slow_reencode)
    threads = [
        threading.Thread(target=attachments.prepare, args=(image_bytes(), None)) for _ in range(4)
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert peak == 1


def test_gif_over_the_pixel_cap_is_kept_as_a_download(monkeypatch):
    monkeypatch.setattr(attachments, "IMAGE_MAX_PIXELS", 100)
    data = image_bytes((20, 20), "GIF")
    assert attachments.prepare(data, "image/gif") == (data, "application/octet-stream", False)
