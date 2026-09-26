"""Make an official exam PDF drawable by the browsers the reviewer actually uses.

`extract.py:web_safe_image` already normalises the *option pictures* because the corpus holds
JPEG 2000 crops that every check passes and no browser draws. The **paper itself** has the same
defect and no such normalisation: the official PDFs embed each scanned page as a `/JPXDecode`
image, Chrome's PDF viewer decodes Flate, JPEG and fax but not JPEG 2000, and the page comes up
white. Measured 2026-09-23, the viewer sits on 「正在擷取 PDF 檔的文字…」 indefinitely with zero
canvases drawn - which a reviewer reads as "still loading", so the report is "won't display, and
it's slow". Adobe and Preview open the same file, which is why the download looked fine.

133 of 8,295 official PDFs carry an un-drawable image; all 133 are question sheets. 35 are in the
currently served queue.

The rewrite touches *only* the image streams: text, fonts, vectors, annotations and page geometry
are copied. No page is rasterised, scaled or re-cropped, so the result is the sheet the reviewer
already approved, drawn by an engine that can draw it.
"""

from __future__ import annotations

import hashlib
import io
import os

#: The compression filters Chrome's built-in PDF viewer cannot decode. A page whose *page image*
#: uses one of these is blank, not degraded - the scanned papers put the whole page in one image.
UNDRAWABLE_FILTERS = frozenset({"/JPXDecode", "/JBIG2Decode"})

#: What the paper is rewritten into. JPEG because every viewer draws it and the source images are
#: photographic scans; Flate output would be several times larger for no gain in legibility.
TARGET_FILTER = "/DCTDecode"

#: Below this, JPEG artefacts start eating thin CJK strokes on a 300 dpi scan.
JPEG_QUALITY = 92

#: `pikepdf` is imported inside the functions that rewrite a paper, never at module import.
#:
#: This module is imported by the *server* (`review_ui/paths.py`) for one constant - the directory
#: name of the derived tree. Requiring a PDF library just to learn a string made the review server
#: stop booting the moment this module landed, because the serving image (correctly) installs
#: `psycopg` and nothing else. Reading and rewriting a PDF is a build-time job; serving one is not.
DEFERRED_DEPENDENCY = "pikepdf"


def _read_bytes(path):
    with open(path, "rb") as handle:
        return handle.read()


def sha256_of(path):
    """The file's digest, streamed so a large paper does not have to fit in memory twice."""
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _filter_names(obj):
    """The `/Filter` entries of a PDF stream object, as plain strings.

    `str()` on a `pikepdf.Name` yields its `/Name` form, which is what the comparison needs:
    `pikepdf.Name` is not a `str` subclass, so testing membership against a set of strings raises
    rather than returning False. A single filter is returned unwrapped by the PDF, a chain as a
    list, and a stream with no images at all has neither.
    """
    raw = obj.get("/Filter")
    if raw is None:
        return []
    if isinstance(raw, (list, tuple)) or hasattr(raw, "__iter__") and not isinstance(raw, str):
        try:
            return [str(name) for name in raw]
        except TypeError:
            pass
    return [str(raw)]


def undrawable_streams(pdf):
    """Every image object in `pdf` whose filter a browser cannot decode, as `(page_no, name, obj)`.

    `page.get_images()` rather than `page.images`: the former also finds images nested in form
    XObjects, which is where a wrapped scan can hide. Counted by object, so an image referenced
    from two pages is reported once per page rather than silently once overall.
    """
    found = []
    for page_no, page in enumerate(pdf.pages, start=1):
        for name, obj in page.get_images().items():
            if any(filter_name in UNDRAWABLE_FILTERS for filter_name in _filter_names(obj)):
                found.append((page_no, name, obj))
    return found


def paper_needs_rewrite(path):
    """True when a browser would show this paper blank. Cheap enough to call over a whole corpus.

    The filters live in the object dictionaries that are uncompressed in every PDF this corpus
    produces, so the bytes are searched directly: opening 8,295 documents to answer a yes/no
    question costs minutes, reading them costs seconds.
    """
    data = _read_bytes(path)
    return any(name.encode() in data for name in UNDRAWABLE_FILTERS)


def _to_jpeg(raw):
    """`raw` image bytes re-encoded as JPEG, or `(None, why)` when Pillow cannot read them."""
    from PIL import Image

    try:
        with Image.open(io.BytesIO(raw)) as image:
            image.load()
            # JPEG has no alpha channel. A scan with a soft mask composites onto white, which is
            # what the mask means; leaving the alpha in would come back as a black page.
            if image.mode in ("RGBA", "LA", "PA"):
                background = Image.new("RGB", image.size, (255, 255, 255))
                background.paste(image, mask=image.split()[-1])
                image = background
            elif image.mode not in ("RGB", "L"):
                image = image.convert("RGB")

            buffer = io.BytesIO()
            image.save(buffer, format="JPEG", quality=JPEG_QUALITY, optimize=True)
            return buffer.getvalue(), None
    except Exception as exc:  # noqa: BLE001 - reported to the caller, never swallowed
        return None, "%s: %s" % (type(exc).__name__, exc)


def rewrite_pdf(source, target):
    """Write `source` to `target` with every un-drawable page image re-encoded as JPEG.

    Returns a report dict. `failures` names the images that could not be re-encoded; they are left
    as they were so the loss is visible in the report rather than silent in the file. The caller
    decides whether that is acceptable - `verify_pdf` refuses an output that still blanks a page.

    Raises `ValueError` when there is nothing to rewrite, so a batch job cannot mistake a no-op for
    success and then report a converted count that was never earned.
    """
    import pikepdf

    with pikepdf.open(source) as pdf:
        pages = len(pdf.pages)
        converted = 0
        failures = []

        for page_no, name, obj in undrawable_streams(pdf):
            raw = bytes(obj.read_raw_bytes())
            data, why = _to_jpeg(raw)
            if data is None:
                failures.append({"page": page_no, "image": str(name), "reason": why})
                continue

            obj.write(data, filter=pikepdf.Name(TARGET_FILTER))
            obj.ColorSpace = pikepdf.Name("/DeviceRGB")
            obj.BitsPerComponent = 8
            # A /DecodeParms array sized for JPX would be read as JPEG parameters and misread the
            # scan; the new stream carries its own headers.
            try:
                del obj.DecodeParms
            except (AttributeError, KeyError):
                pass
            converted += 1

        if converted == 0 and not failures:
            raise ValueError("no un-drawable image streams: %s" % source)

        os.makedirs(os.path.dirname(os.path.abspath(target)) or ".", exist_ok=True)
        pdf.save(target, linearize=True, compress_streams=True)

    return {
        "source": source,
        "target": target,
        "pages": pages,
        "converted_images": converted,
        "failures": failures,
        "source_bytes": os.path.getsize(source),
        "target_bytes": os.path.getsize(target),
        "source_sha256": sha256_of(source),
        "target_sha256": sha256_of(target),
    }


def verify_pdf(report):
    """Read the output back and list what is wrong with it. Empty list means it is safe to serve.

    Checking the *output* rather than trusting the write is the whole point: the original defect was
    a file that passed every structural check and rendered nothing.
    """
    import pikepdf

    problems = []

    with open(report["target"], "rb") as handle:
        if handle.read(5) != b"%PDF-":
            problems.append("不是 PDF 檔")
            return problems

    with pikepdf.open(report["target"]) as pdf:
        if len(pdf.pages) != report["pages"]:
            problems.append("頁數改變: %d -> %d" % (report["pages"], len(pdf.pages)))

        remaining = undrawable_streams(pdf)
        if remaining:
            problems.append("仍有 %d 張無法解碼的圖片" % len(remaining))

        drew = any(
            str(obj.get("/Filter")) == TARGET_FILTER
            for page in pdf.pages
            for obj in page.get_images().values()
        )
        if not drew and report["converted_images"]:
            problems.append("報告說轉了 %d 張，輸出裡卻沒有 JPEG" % report["converted_images"])

    if report["target_sha256"] == report["source_sha256"]:
        problems.append("輸出與來源完全相同（轉換沒有發生）")

    return problems


#: A paper that is still blank after rewriting is worse than one that was never touched, because
#: the report would claim it was fixed.
def rewrite_and_verify(source, target):
    """`rewrite_pdf` followed by `verify_pdf`, raising when the output is not servable."""
    report = rewrite_pdf(source, target)
    problems = verify_pdf(report)
    report["problems"] = problems
    report["verified"] = not problems
    if problems:
        raise ValueError("%s: %s" % (target, "; ".join(problems)))
    return report


def corpus_pages_needing_rewrite(asset_root_path, suffixes=("*.pdf",)):
    """Every PDF under `asset_root_path` that a browser cannot draw, as paths, sorted.

    Not a generator: the caller needs to know the size of the job before it starts writing.
    """
    hits = []
    for directory, _dirs, names in os.walk(asset_root_path):
        for name in names:
            if not name.endswith(".pdf"):
                continue
            path = os.path.join(directory, name)
            try:
                if paper_needs_rewrite(path):
                    hits.append(path)
            except OSError:
                continue
    return sorted(hits)


#: Where a rewritten paper lives inside the queue's asset root. The official corpus is never
#: modified - it is the source of truth and carries its own digests - so the servable copy is a
#: sibling tree the review server prefers.
DERIVED_DIR_NAME = "10_official_pdf_browser_safe"


def derived_path_for(source, asset_root_path, derived_root=None):
    """Where the browser-safe copy of `source` belongs, preserving its path below the corpus root.

    Mirroring the tree rather than flattening it keeps the relative path a stable identifier: the
    review queue records `official_pdf` as a corpus-relative path, and the server can swap the
    prefix without rewriting a single queue row.
    """
    relative = os.path.relpath(source, asset_root_path)
    root = derived_root or os.path.join(asset_root_path, DERIVED_DIR_NAME)
    return os.path.join(root, relative)
