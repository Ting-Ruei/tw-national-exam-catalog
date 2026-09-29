"""測試要交給 node 的那份 JS，走的是暫存檔，不是命令列的參數。

為什麼要一個測試守住這件事：Linux 對**單一 `argv` 字串**有 128 KiB 的上限（`MAX_ARG_STRLEN`），
macOS 沒有。把整份 script（v2 的全部原始碼、或一整棵範圍樹的 JSON）塞進 `node -e` 在筆電上永遠
不會出錯，在 CI 的 Ubuntu 上卻會直接 `OSError: [Errno 7] Argument list too long`——量到：
`tests/test_review_ui_discuss.py::test_the_merged_bucket_is_checked_against_the_real_helpers`
在 Linux 容器（node v20.19.2）裡 100% 失敗、在 macOS 上 100% 通過。也就是說**只有 CI 看得見**。

兩條契約：
  1. 一份**超過** 128 KiB 的 script 仍然跑得動（走檔案就沒有這個上限）。
  2. 交給 `subprocess` 的每一個參數都遠小於 128 KiB（負對照：舊寫法會把整份 script 當成一個參數，
     這一條就會失敗）。
"""
from __future__ import annotations

import json
import unittest
from unittest import mock

from review_ui_source import NODE_ARG_MAX, run_node_script


class NodeScriptHandoffTests(unittest.TestCase):
    def test_a_script_larger_than_the_linux_argument_limit_still_runs(self):
        filler = "// " + ("x" * 200) + "\n"
        script = filler * 2000 + 'console.log(JSON.stringify({ok: true, length: %d}));' % (len(filler) * 2000)
        self.assertGreater(len(script), NODE_ARG_MAX, "這個測試要的是一份超過 Linux 單一 argv 上限的 script")
        self.assertEqual(json.loads(run_node_script(script))["ok"], True)

    def test_the_script_is_never_handed_over_as_a_command_line_argument(self):
        seen: list[list[str]] = []
        real_run = __import__("subprocess").run

        def spy(argv, *args, **kwargs):
            seen.append(list(argv))
            return real_run(argv, *args, **kwargs)

        with mock.patch("review_ui_source.subprocess.run", side_effect=spy) as patched:
            run_node_script(
                'console.log(JSON.stringify(process.argv[1] ?? null));', args=["tiny"]
            )
        self.assertTrue(patched.called)
        self.assertEqual(len(seen), 1, "應該只呼叫 subprocess.run 一次")
        argv = seen[0]
        self.assertNotIn("-e", argv, "script 不可以走 `node -e`")
        self.assertEqual(argv[1:], [argv[1], "tiny"], "argv 尾巴之外只該多一個暫存檔路徑")
        for argument in argv:
            self.assertLess(len(argument), NODE_ARG_MAX, f"參數太長（Linux 會拒絕）：{argument[:40]}…")

    def test_a_small_argument_tail_reaches_the_script(self):
        stdout = run_node_script('console.log(JSON.stringify(process.argv.slice(2)));', args=["a", "b"])
        self.assertEqual(json.loads(stdout), ["a", "b"])


if __name__ == "__main__":
    unittest.main()
