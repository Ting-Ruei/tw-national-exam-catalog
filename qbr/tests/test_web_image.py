"""Crops must be in a format a browser will actually draw.

The defect this covers produced no error anywhere in the pipeline: the file existed, the server
returned HTTP 200, and the reviewer saw an empty box with the alt text beside it. Measured on the
served queue, 44 option pictures are JPEG 2000, which Chrome does not render under any declared
type - so the container is normalized at the point the crop is written.
"""
import io
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "src"))

from qbr import extract


def _png(width=4, height=4):
    from PIL import Image
    buffer = io.BytesIO()
    Image.new("RGB", (width, height), "white").save(buffer, format="PNG")
    return buffer.getvalue()


def _jpeg():
    from PIL import Image
    buffer = io.BytesIO()
    Image.new("RGB", (8, 8), "red").save(buffer, format="JPEG")
    return buffer.getvalue()


def _jpx():
    from PIL import Image
    buffer = io.BytesIO()
    Image.new("RGB", (8, 8), "blue").save(buffer, format="JPEG2000")
    return buffer.getvalue()


def test_a_png_is_passed_through_untouched():
    # The hand-cut reference bank is byte-identical to the embedded object; re-encoding it would
    # break that correspondence for no reason.
    blob = _png()
    out, suffix = extract.web_safe_image(blob, ".png")
    assert out == blob and suffix == ".png"


def test_a_jpeg_keeps_its_own_container():
    blob = _jpeg()
    out, suffix = extract.web_safe_image(blob, ".jpeg")
    assert out == blob and suffix == ".jpeg"


def test_jpeg2000_is_converted_because_no_browser_renders_it():
    blob = _jpx()
    out, suffix = extract.web_safe_image(blob, ".jpx")
    assert suffix == ".png"
    assert out[:8] == b"\x89PNG\r\n\x1a\n"
    assert out != blob


def test_an_unknown_container_is_converted_rather_than_kept():
    out, suffix = extract.web_safe_image(_jpx(), ".jp2")
    assert suffix == ".png" and out[:8] == b"\x89PNG\r\n\x1a\n"


def test_something_that_cannot_be_decoded_is_still_returned():
    # Losing the picture silently is worse than writing an odd file: a missing crop is invisible,
    # an unconvertible one can be looked at.
    blob = b"not an image at all"
    out, suffix = extract.web_safe_image(blob, ".jpx")
    assert out == blob


def test_the_web_format_set_covers_what_browsers_draw():
    assert {"png", "jpg", "jpeg", "gif", "webp", "bmp"} <= extract.WEB_IMAGE_FORMATS
    assert "jpx" not in extract.WEB_IMAGE_FORMATS
