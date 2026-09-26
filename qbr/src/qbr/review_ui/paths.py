"""Review-UI path and content-type helpers.

Extracted verbatim from `scripts/serve_question_review_ui.py` so one file is no longer 10,700 lines.
`serve_question_review_ui` re-exports every name here; that indirection is deliberate and is why the
test files that load the server by path keep working unchanged. No behaviour was changed.
"""

from __future__ import annotations



import sys as _sys
from pathlib import Path as _Path

# The server's guarded imports fall back to `from scripts.x import ...`. `scripts/` has no
# `__init__.py`, so that is a namespace-package import and needs the *repository root* on `sys.path`
# — not the `scripts/` directory. Both are added: the root for `scripts.x`, the directory for the
# plain `import x` form that a bare `sys.path` entry would otherwise be needed for.
_REPO_ROOT = str(_Path(__file__).resolve().parents[4])
for _p in (_REPO_ROOT, _REPO_ROOT + "/scripts"):
    if _p not in _sys.path:
        _sys.path.insert(0, _p)

from typing import Any
from pathlib import Path
import base64
import mimetypes
import os
import re
from .constants import ASSET_ROOT, PROJECT_ROOT, STRUCTURED_TABLE_RE

def project_path(value: str) -> Path:
    path = Path(value).expanduser()
    parts = path.parts
    if "國考題資料夾" in parts:
        index = parts.index("國考題資料夾")
        return ASSET_ROOT.joinpath(*parts[index + 1 :])
    if path.is_absolute():
        try:
            direct = path.resolve()
        except OSError:
            direct = path
        roots = (PROJECT_ROOT.resolve(), ASSET_ROOT.resolve())
        if any(direct == root or root in direct.parents for root in roots):
            return direct
        if "tw-national-exam-catalog" in parts:
            index = len(parts) - 1 - parts[::-1].index("tw-national-exam-catalog")
            return PROJECT_ROOT.joinpath(*parts[index + 1 :])
        return path
    if value.startswith("國考題資料夾/"):
        return ASSET_ROOT / value.removeprefix("國考題資料夾/")
    return ASSET_ROOT / value


def content_type_of(name: str, data: bytes) -> str:
    """The type to send for a file, from its bytes and only then from its name.

    A file's extension is a claim, and the claim can be wrong. Measured on this corpus: 44 option
    pictures are stored as JPEG 2000 but named `*.jpx` while one earlier run named them `*.png`, and
    a browser will not render JPEG 2000 under any type. The name alone therefore cannot decide what
    to say, because saying `image/png` for bytes that are not PNG makes Chrome refuse to draw them
    and the reviewer gets an empty box with the alt text showing - the image "not displaying".

    The bytes are checked first, and the name is the fallback. This cannot make a bad image good; it
    can only stop the server from actively mislabelling one.
    """
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return "image/png"
    if data[:3] == b"\xff\xd8\xff":
        return "image/jpeg"
    if data[:6] in (b"GIF87a", b"GIF89a"):
        return "image/gif"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    if data[:2] == b"BM":
        return "image/bmp"
    if data[:4] == b"%PDF":
        return "application/pdf"
    return mimetypes.guess_type(name)[0] or "application/octet-stream"


def safe_file_path(value: str) -> Path | None:
    if not value:
        return None
    path = project_path(value)
    try:
        resolved = path.resolve()
    except FileNotFoundError:
        return None
    allowed_roots = [ASSET_ROOT.resolve()]
    # Local staged runs keep derived PNGs separate from the source archive.
    # Explicit roots avoid granting access to the entire project (and .env).
    extra_roots = [Path(raw).expanduser().resolve() for raw in
                   os.environ.get("REVIEW_UI_ADDITIONAL_ASSET_ROOTS", "").split(os.pathsep) if raw]
    allowed_roots.extend(extra_roots)
    if not resolved.is_file() and not Path(value).is_absolute():
        for root in extra_roots:
            candidate = (root / value).resolve()
            if candidate.is_relative_to(root) and candidate.is_file():
                resolved = candidate
                break
    if os.environ.get("REVIEW_UI_ALLOW_PROJECT_FILES", "0").lower() in {"1", "true", "yes"}:
        allowed_roots.append(PROJECT_ROOT.resolve())
    if any(resolved == root or root in resolved.parents for root in allowed_roots):
        return resolved
    return None


def display_path(path: Path | None) -> str | None:
    if path is None:
        return None
    try:
        return str(path.resolve().relative_to(PROJECT_ROOT.resolve()))
    except ValueError:
        return str(path)


def sibling_pdf(markdown_value: str, suffix: str) -> str | None:
    if not markdown_value:
        return None
    markdown_path = project_path(markdown_value)
    candidate = markdown_path.with_name(f"{markdown_path.stem}{suffix}.pdf")
    if candidate.exists():
        return display_path(candidate)
    return None


def safe_path_segment(value: Any, fallback: str = "unknown") -> str:
    text = str(value or "").strip()
    text = re.sub(r"[\\/:\*\?\"<>\|\s]+", "_", text)
    text = re.sub(r"_+", "_", text).strip("._")
    return text[:80] or fallback


def data_url_to_bytes(data_url: str) -> tuple[bytes, str, str]:
    match = re.fullmatch(r"data:(image/(?:png|jpeg|jpg|webp));base64,(.+)", data_url.strip(), re.S)
    if not match:
        raise ValueError("Only png, jpeg, or webp image data URLs are supported.")
    mime_type = "image/jpeg" if match.group(1) == "image/jpg" else match.group(1)
    extension = {
        "image/png": ".png",
        "image/jpeg": ".jpg",
        "image/webp": ".webp",
    }[mime_type]
    try:
        data = base64.b64decode(match.group(2), validate=True)
    except Exception as exc:
        raise ValueError("Image data is not valid base64.") from exc
    if not data:
        raise ValueError("Image is empty.")
    if len(data) > 15 * 1024 * 1024:
        raise ValueError("Image is larger than 15 MB.")
    return data, mime_type, extension


def strip_structured_tables(value: str) -> tuple[str, bool]:
    if not value:
        return value, False
    stripped = STRUCTURED_TABLE_RE.sub("", value)
    stripped = re.sub(r"\n{3,}", "\n\n", stripped).strip()
    return stripped, stripped != value
