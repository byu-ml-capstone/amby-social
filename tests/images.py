import io

from PIL import Image


def image_bytes(size=(40, 30), fmt="PNG", **save) -> bytes:
    out = io.BytesIO()
    Image.new("RGB", size, "red").save(out, fmt, **save)
    return out.getvalue()
