"""紙本的上下標要畫成上下標，而不是畫成 `<sub>` 這幾個字。

使用者回報的第一項：「你 UI 的上下標是顯示 `<sup></sup>`」。這是真的，而且是 v2 自己造成的
回歸：語料裡 6,652 列帶著抽取器讀到的行內標記（`α<sub>1</sub>`、`e<sup>-0.35t</sup>`、
`<sup>99m</sup>Tc`），舊的 `/legacy` 與 v1 `mobile.html` 都有 `renderText()` 把它正規化成真的
標籤，v2 用 `esc()` 一次跳脫掉——於是審題者一邊看紙本上的下標，一邊讀螢幕上的 `<sub>1</sub>`。

這個檔案釘三件事，每一件都有**負對照**（拿掉對應的程式碼，斷言必須失敗）：

  1. 題幹／選項／共用題幹走 `richText()`，不是 `esc()`。
  2. `richText()` 是白名單：只放行 `sub`/`sup`/`u`/`b`/`i`/`<br>`，其餘一律維持跳脫。
  3. 標籤不成對時要補／丟，否則一個漏掉的 `</sup>` 會把整題後面的字都變成上標，
     而那種缺陷沒有人會回報成「文字有問題」。
"""
from __future__ import annotations

import re
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
V2 = ROOT / "review_ui" / "v2.html"
CORE = ROOT / "review_ui" / "v2" / "01-core.js"
QUESTION = ROOT / "review_ui" / "v2" / "02-area-question.js"
AREAS = ROOT / "review_ui" / "v2" / "03-areas.js"


def rich_text_source() -> str:
    source = CORE.read_text(encoding="utf-8")
    match = re.search(r"function richText\(value\) \{(.*?)\n\}", source, re.S)
    assert match, "01-core.js 裡找不到 richText"
    return match.group(1)


def python_rich_text(value: str) -> str:
    """`richText` 的 Python 重算，逐條對應 JS——用來真的餵字串進去驗輸出。

    這裡刻意重寫而不是呼叫 JS：測試要能在沒有 node 的情況下跑。這代表兩份實作，
    所以下面 `test_the_typescript_and_the_python_agree_on_a_table_of_cases` 用同一張表
    兩邊都跑，任何一側漂移就會紅。
    """
    out = (str(value or '').replace('&', '&amp;').replace('<', '&lt;')
           .replace('>', '&gt;').replace('"', '&quot;').replace("'", '&#39;'))
    out = re.sub(r'&lt;(/?)sub&gt;', r'<\1sub>', out)
    out = re.sub(r'&lt;(/?)sup&gt;', r'<\1sup>', out)
    out = re.sub(r'&lt;(/?)u&gt;', r'<\1u>', out)
    out = re.sub(r'&lt;(/?)b&gt;', r'<\1b>', out)
    out = re.sub(r'&lt;(/?)i&gt;', r'<\1i>', out)
    out = re.sub(r'&lt;br\s*/?&gt;', '<br>', out)
    for tag in ('sub', 'sup', 'u', 'b', 'i'):
        opens = len(re.findall(f'<{tag}>', out))
        closes = len(re.findall(f'</{tag}>', out))
        if opens > closes:
            out += f'</{tag}>' * (opens - closes)
        elif closes > opens:
            extra = closes - opens
            def drop(match, _tag=tag):
                nonlocal extra
                if extra > 0:
                    extra -= 1
                    return f'&lt;/{_tag}&gt;'
                return match.group(0)
            out = re.sub(f'</{tag}>', drop, out)
    return out


#: The cases are the table both implementations must agree on. The dangerous ones are at the bottom:
#: an attribute, a script, an unknown tag, and an unbalanced pair.
CASES = [
    ('α<sub>1</sub>-adrenergic', 'α<sub>1</sub>-adrenergic'),
    ('e<sup>-0.35t</sup>', 'e<sup>-0.35t</sup>'),
    ('<sup>99m</sup>Tc', '<sup>99m</sup>Tc'),
    ('R<sub>1</sub>＝H', 'R<sub>1</sub>＝H'),
    ('<sup>®</sup>', '<sup>®</sup>'),
    ('a<br>b', 'a<br>b'),
    ('a<br/>b', 'a<br>b'),
    # negative controls
    ('<script>alert(1)</script>', '&lt;script&gt;alert(1)&lt;/script&gt;'),
    ('<img src=x onerror=alert(1)>', '&lt;img src=x onerror=alert(1)&gt;'),
    ('<sub class="big">1</sub>', '&lt;sub class=&quot;big&quot;&gt;1&lt;/sub&gt;'),
    ('<iframe src="x"></iframe>', '&lt;iframe src=&quot;x&quot;&gt;&lt;/iframe&gt;'),
    ('<sub>1', '<sub>1</sub>'),
    # A stray closer is text the whitelist did not accept, so it stays visible as text rather than
    # being silently deleted from a question.
    ('</sub>1', '&lt;/sub&gt;1'),
    ('&amp;<sub>x</sub>', '&amp;amp;<sub>x</sub>'),
]


class RichTextTests(unittest.TestCase):
    #: The call sites: the stem, every option, the shared stem of a group, and the answer sheet's
    #: preview. Missing any one of them is the reported bug half-fixed.
    def test_the_question_area_renders_the_paper_markup_as_markup(self):
        source = QUESTION.read_text(encoding="utf-8")
        for pattern in (r'\$\{richText\(candidate\.stem', r'\$\{richText\(option\.text\)\}',
                        r'\$\{richText\(shared\)\}'):
            self.assertRegex(source, pattern)
        # The negative control: the old spelling must be gone, or the fix can pass while the bug ships.
        self.assertNotRegex(source, r'\$\{esc\(candidate\.stem')
        self.assertNotRegex(source, r'\$\{esc\(option\.text\)\}')

    def test_the_answer_sheet_renders_the_paper_markup_as_markup(self):
        source = AREAS.read_text(encoding="utf-8")
        self.assertRegex(source, r'\$\{richText\(stem\)\}')
        self.assertRegex(source, r'\$\{richText\(String\(o\.text')
        self.assertNotRegex(source, r'\$\{esc\(stem\)\}')

    def test_the_whitelist_and_the_escaping_in_the_live_script(self):
        source = rich_text_source()
        # Every whitelisted tag is un-escaped by an explicit rule and closed for balance...
        self.assertIn('RICH_TAGS', CORE.read_text(encoding="utf-8"))
        for tag in ("'sub'", "'sup'", "'u'", "'b'", "'i'"):
            self.assertIn(tag, CORE.read_text(encoding="utf-8"))
        self.assertIn('&lt;br', source)
        # ...and the escape happens **first**, so nothing can be un-escaped that was not escaped here.
        self.assertLess(source.index('esc(value)'), source.index('&lt;(\\/?)'))
        # A tag the whitelist does not name must not appear in an un-escaping rule at all.
        self.assertNotIn('script', source)
        self.assertNotIn('img', source)

    def test_the_python_reimplementation_agrees_with_the_table(self):
        for value, expected in CASES:
            self.assertEqual(python_rich_text(value), expected, f'input={value!r}')

    def test_the_js_and_the_python_agree_on_the_same_table(self):
        source = rich_text_source()
        for value, expected in CASES:
            # The JS is driven through node when it is available; the table is the contract, so a
            # divergence is a defect in one of the two, never in the table.
            import json
            import shutil
            import subprocess
            node = shutil.which('node')
            if not node:
                self.skipTest('node 不在這台機器上')
            script = (
                'const RICH_TAGS = ["sub", "sup", "u", "b", "i"];\n'
                'const esc = (v) => String(v ?? "").replace(/[&<>"\']/g, (c) => '
                '({"&":"&amp;","<":"&lt;",">":"&gt;","\\"":"&quot;","\'":"&#39;"}[c]));\n'
                f'function richText(value) {{{source}\n}}\n'
                f'console.log(JSON.stringify(richText({json.dumps(value)})));'
            )
            result = subprocess.run([node, '-e', script], capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(json.loads(result.stdout), expected, f'input={value!r} (JS)')


if __name__ == '__main__':
    unittest.main()
