"""An official paper must be drawable by the browser the reviewer actually uses.

The defect this pins down, measured 2026-09-23: the official PDFs embed each scanned page as a
JPEG 2000 image (`/JPXDecode`). Chrome's built-in PDF viewer decodes Flate, JPEG and fax, but not
JPEG 2000, so every page of such a paper comes up white while the viewer sits on
「正在擷取 PDF 檔的文字…」 with zero canvases drawn. Adobe and Preview open the same file, so it
passed every download and every structural check and still showed the reviewer nothing.

That is the same shape as the `web_safe_image` defect in `extract.py` - "a crop that exists, is
served with HTTP 200, and still shows nothing" - but on the paper rather than on the options. The
fix is the same kind of thing: normalise the container at build time, prefer the normalised copy
when serving, and never modify the official corpus.

These tests assert on the property a viewer depends on (which filters its page images use) and not
on how the rewrite is written, because the point is that *some* engine can draw the page.
"""

from __future__ import annotations

import os
import sys
import tempfile
import unittest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO_ROOT, "qbr", "src"))

from qbr import browser_safe_pdf  # noqa: E402

try:
    import pikepdf
except ImportError:  # pragma: no cover - the dependency is declared in requirements/qbr.txt
    pikepdf = None


def _filters_of(path):
    """The `/Filter` of every image in `path`, as plain strings."""
    with pikepdf.open(path) as pdf:
        found = []
        for page in pdf.pages:
            for obj in page.get_images().values():
                raw = obj.get("/Filter")
                names = [raw] if isinstance(raw, pikepdf.Name) else list(raw or [])
                found.extend(str(name) for name in names)
        return found


def _jpx_pdf(path, pages=2):
    """A PDF whose pages are JPEG 2000 images - the shape the official papers have.

    Built here rather than checked in: the corpus is gitignored and large, and a two-page fixture
    is enough to exercise "does the rewrite remove the un-drawable filter".
    """
    import io

    from PIL import Image

    with pikepdf.new() as pdf:
        for index in range(pages):
            image = Image.new("RGB", (120, 80), (255 - index * 40, 200, 180))
            buffer = io.BytesIO()
            # JPEG 2000 is what the scans use and what Chrome cannot decode.
            image.save(buffer, format="JPEG2000", quality_mode="rates", quality_layers=[20])
            stream = pdf.make_stream(buffer.getvalue())
            stream.Type = pikepdf.Name("/XObject")
            stream.Subtype = pikepdf.Name("/Image")
            stream.Width = 120
            stream.Height = 80
            stream.ColorSpace = pikepdf.Name("/DeviceRGB")
            stream.BitsPerComponent = 8
            stream.Filter = pikepdf.Name("/JPXDecode")

            page = pdf.add_blank_page(page_size=(300, 200))
            page.Resources = pikepdf.Dictionary(
                XObject=pikepdf.Dictionary(Im0=stream)
            )
            page.Contents = pdf.make_stream(b"q 300 0 0 200 0 0 cm /Im0 Do Q")
        pdf.save(path)


@unittest.skipIf(pikepdf is None, "pikepdf is required to inspect PDF filters")
class BrowserSafePaperTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.source = os.path.join(self.tmp.name, "source.pdf")
        self.target = os.path.join(self.tmp.name, "out", "target.pdf")
        _jpx_pdf(self.source)

    def test_the_fixture_really_is_un_drawable(self):
        """Negative control: if this passes trivially the rest of the file proves nothing."""
        self.assertIn("/JPXDecode", _filters_of(self.source))
        self.assertTrue(browser_safe_pdf.paper_needs_rewrite(self.source))

    def test_rewrite_removes_the_undrawable_filter(self):
        """The property a viewer depends on: no page image carries a filter Chrome cannot decode."""
        report = browser_safe_pdf.rewrite_and_verify(self.source, self.target)

        filters = _filters_of(self.target)
        self.assertNotIn("/JPXDecode", filters)
        self.assertIn(browser_safe_pdf.TARGET_FILTER, filters)
        self.assertEqual(report["converted_images"], 2)
        self.assertTrue(report["verified"])
        self.assertEqual(report["problems"], [])

    def test_rewrite_preserves_page_count(self):
        report = browser_safe_pdf.rewrite_pdf(self.source, self.target)
        self.assertEqual(report["pages"], 2)
        with pikepdf.open(self.target) as pdf:
            self.assertEqual(len(pdf.pages), 2)

    def test_rewrite_does_not_touch_the_source(self):
        """The official corpus is the source of truth; the fix must never write into it."""
        before = browser_safe_pdf.sha256_of(self.source)
        browser_safe_pdf.rewrite_pdf(self.source, self.target)
        self.assertEqual(browser_safe_pdf.sha256_of(self.source), before)

    def test_verification_rejects_an_output_that_still_blanks(self):
        """A rewrite that silently did nothing must not be reportable as success."""
        report = browser_safe_pdf.rewrite_pdf(self.source, self.target)
        # Claim the output is the untouched source: identical digests is the "no-op" signature.
        lying = dict(report, target_sha256=report["source_sha256"])
        self.assertIn(
            "輸出與來源完全相同（轉換沒有發生）",
            browser_safe_pdf.verify_pdf(lying),
        )

    def test_verification_rejects_a_truncated_output(self):
        report = browser_safe_pdf.rewrite_pdf(self.source, self.target)
        with open(self.target, "wb") as handle:
            handle.write(b"not a pdf")
        self.assertIn("不是 PDF 檔", browser_safe_pdf.verify_pdf(report))

    def test_a_paper_needing_no_rewrite_raises_rather_than_reporting_success(self):
        """A batch must not count a no-op as a conversion."""
        plain = os.path.join(self.tmp.name, "plain.pdf")
        with pikepdf.new() as pdf:
            pdf.add_blank_page(page_size=(200, 200))
            pdf.save(plain)

        self.assertFalse(browser_safe_pdf.paper_needs_rewrite(plain))
        with self.assertRaises(ValueError):
            browser_safe_pdf.rewrite_pdf(plain, os.path.join(self.tmp.name, "plain-out.pdf"))

    def test_derived_path_mirrors_the_corpus_tree(self):
        """Mirroring keeps the corpus-relative path a stable identifier, so no queue row changes."""
        root = os.path.join(self.tmp.name, "國考題資料夾")
        source = os.path.join(root, "10_official_pdf", "by_official_catalog", "藥師(一)", "a.pdf")
        derived = browser_safe_pdf.derived_path_for(source, root)
        self.assertEqual(
            os.path.relpath(derived, root),
            os.path.join(
                browser_safe_pdf.DERIVED_DIR_NAME,
                "10_official_pdf", "by_official_catalog", "藥師(一)", "a.pdf",
            ),
        )


@unittest.skipIf(pikepdf is None, "pikepdf is required to inspect PDF filters")
class ServerPrefersTheDrawableCopyTest(unittest.TestCase):
    """The rewrite only helps if the server asks for it."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.asset_root = os.path.join(self.tmp.name, "國考題資料夾")
        self.relative = os.path.join("10_official_pdf", "by_official_catalog", "藥師", "p.pdf")
        self.original = os.path.join(self.asset_root, self.relative)
        os.makedirs(os.path.dirname(self.original), exist_ok=True)
        _jpx_pdf(self.original, pages=1)

        # `paths` reads ASSET_ROOT at import time; point it at the fixture for this test.
        os.environ["ASSET_ROOT"] = self.asset_root
        import qbr.review_ui.paths as paths_module

        self.paths = paths_module
        self._saved_root = paths_module.ASSET_ROOT
        paths_module.ASSET_ROOT = type(paths_module.ASSET_ROOT)(self.asset_root)
        self.addCleanup(setattr, paths_module, "ASSET_ROOT", self._saved_root)

    def test_without_a_derived_copy_the_original_is_served(self):
        resolved = self.paths.safe_file_path(self.relative)
        self.assertIsNotNone(resolved)
        self.assertEqual(str(resolved), os.path.realpath(self.original))

    def test_with_a_derived_copy_the_drawable_version_is_served(self):
        derived = browser_safe_pdf.derived_path_for(self.original, self.asset_root)
        os.makedirs(os.path.dirname(derived), exist_ok=True)
        browser_safe_pdf.rewrite_and_verify(self.original, derived)

        resolved = self.paths.safe_file_path(self.relative)
        self.assertIsNotNone(resolved)
        self.assertEqual(str(resolved), os.path.realpath(derived))
        self.assertNotIn("/JPXDecode", _filters_of(resolved))

    def test_the_original_is_still_readable_after_serving_the_derived_copy(self):
        """The corpus keeps its own bytes; only the served path moves."""
        derived = browser_safe_pdf.derived_path_for(self.original, self.asset_root)
        os.makedirs(os.path.dirname(derived), exist_ok=True)
        browser_safe_pdf.rewrite_and_verify(self.original, derived)
        self.paths.safe_file_path(self.relative)
        self.assertIn("/JPXDecode", _filters_of(self.original))


if __name__ == "__main__":
    unittest.main()
