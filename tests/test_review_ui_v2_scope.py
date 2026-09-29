"""篩選某一個層級，不可以動到其他層級。

使用者回報：「題目審核區的下拉式選單，當我選了某個科系，不要去動到其他選項，這樣我才能精準
篩選，不然每次都跳來跳去。」

這是真的，而且量得到。v2 的每個 `onchange` 都把下層設成 `null`：

    category.onchange → year=sitting=subject=null
    year.onchange     → sitting=subject=null
    sitting.onchange  → subject=null

`null` 的定義是「還沒選」，所以 `resolveLevel` 會替它挑第一個選項——也就是**具體的**某一年／
某一次／某一科。在真 Chrome 上量到：把「考次」改回 `全部考次`，同一個事件裡「科目」從 `''`
（全部）變成 `藥學(一)(包括藥理學與藥物化學)`。審題者設定的篩選無聲地被換掉，而唯一能發現的
方法就是再把下拉打開來看。

修好之後的契約只有兩條，這個檔案各釘一次，並各帶一個負對照：

  1. `onchange` 只寫自己那一格，不碰其他三格。
  2. 一個值「上層換了之後就不再被提供」時，落點是 **全部**，不是另一個具體值——
     只有 `null`（app 自己選預設）才落第一個值。
"""
from __future__ import annotations

import json
import re
import shutil
import subprocess
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
CORE = ROOT / "review_ui" / "v2" / "01-core.js"
PAGE = ROOT / "review_ui" / "v2.html"


def core_source() -> str:
    return CORE.read_text(encoding="utf-8")


def run_node(expression: str) -> object:
    """在真的 `01-core.js` 上跑一個運算式——不重寫一份 JS，因為重寫的那份不會壞。"""
    node = shutil.which("node")
    if not node:
        raise unittest.SkipTest("node 不在這台機器上")
    source = core_source()
    # The module reads `document` at load time (the handlers are bound to real nodes), so a minimal
    # DOM is stubbed exactly as `scripts/test_v2_navigation.mjs` does.
    stub = """
      globalThis.document = {
        getElementById: () => ({ value:'', options:[], innerHTML:'', textContent:'', style:{},
                                  classList:{add(){},remove(){},toggle(){},contains(){return false}},
                                  addEventListener(){}, querySelectorAll: () => [] }),
        querySelectorAll: () => [], querySelector: () => null, addEventListener() {},
        createElement: () => ({ style:{}, classList:{add(){},remove(){}}, appendChild(){} }),
        body: { appendChild(){} },
      };
      globalThis.location = { hash:'' };
      globalThis.history = { replaceState(){} };
      globalThis.window = { addEventListener(){} };
      globalThis.fetch = async () => ({ ok:false, status:0, json: async () => ({}) });
      globalThis.setTimeout = () => 0; globalThis.clearTimeout = () => {};
    """
    script = f"{stub}\n{source}\nconsole.log(JSON.stringify({expression}));"
    result = subprocess.run([node, "-e", script], capture_output=True, text=True)
    if result.returncode != 0:
        raise AssertionError(f"node 執行失敗：{result.stderr[-2000:]}")
    return json.loads(result.stdout.strip().splitlines()[-1])


class ScopePickerContract(unittest.TestCase):
    def test_the_category_handler_does_not_clear_the_levels_below_it(self):
        body = re.search(r"category\.onchange = \(\) => \{(.*?)\n  \};", core_source(), re.S)
        self.assertIsNotNone(body, "找不到 category.onchange")
        # A negative control in the literal sense: the old three assignments must be gone.
        for gone in ("S.scope.year = null", "S.scope.sitting = null", "S.scope.subject = null"):
            self.assertNotIn(gone, body.group(1), f"{gone} 還在 category.onchange 裡")

    def test_the_year_and_sitting_handlers_only_write_their_own_level(self):
        source = core_source()
        year = re.search(r"year\.onchange = \(\) => \{(.*?)\};", source, re.S)
        sitting = re.search(r"sitting\.onchange = \(\) => \{(.*?)\};", source, re.S)
        self.assertIsNotNone(year, "找不到 year.onchange")
        self.assertIsNotNone(sitting, "找不到 sitting.onchange")
        for name, match in (("year", year), ("sitting", sitting)):
            self.assertNotIn("= null", match.group(1), f"{name}.onchange 還在把別的層級歸零")

    #: The table both the real function and the reading of the contract must agree on.
    #: `offered` is what the level can offer right now; the third column is what the picker must show.
    RESOLVE_CASES = [
        # not chosen yet → the app picks the sensible default (the first value)
        (None, ["115", "114"], "115"),
        # "all" survives, and does not need 全部 to be first
        ("", ["115", "114"], ""),
        # a value that still exists is kept **exactly** - the point of the whole fix
        ("114", ["115", "114"], "114"),
        # a value that stopped existing → 全部 when 全部 is on offer, never a sibling value
        ("107", ["115", "114"], ""),
        # a level with only one value has no 全部 to offer, so a stale value lands on that value
        ("107", ["115"], "115"),
        # an empty level resolves to the empty string rather than `undefined`
        (None, [], ""),
    ]

    def test_resolve_level_keeps_a_chosen_value_and_falls_back_to_all(self):
        for value, offered, expected in self.RESOLVE_CASES:
            got = run_node(f"resolveLevel({json.dumps(value)}, {json.dumps(offered)})")
            self.assertEqual(got, expected, f"resolveLevel({value!r}, {offered!r})")

    def test_the_negative_control_is_the_old_fallback(self):
        """舊寫法：不存在就換成 `offered[0]`。這個負對照證明上面的案例抓得到它。

        對應使用者回報的「跳來跳去」：`''`（全部）在舊寫法裡會被換成 `'115'`。
        """
        def old_resolve(value, offered):
            if value is None:
                return offered[0] if offered else ""
            if value == "":
                return "" if len(offered) > 1 else (offered[0] if offered else "")
            return value if value in offered else (offered[0] if offered else "")

        # The old function moves the picker on exactly the cases the new one holds still.
        self.assertEqual(old_resolve("107", ["115", "114"]), "115")
        self.assertNotEqual(old_resolve("107", ["115", "114"]), "")
        self.assertEqual(old_resolve("", ["115", "114"]), "")
        # And it is the same function on the case the fix is about.
        self.assertNotEqual(old_resolve("107", ["115", "114"]),
                            run_node('resolveLevel("107", ["115", "114"])'))


if __name__ == "__main__":
    unittest.main()
