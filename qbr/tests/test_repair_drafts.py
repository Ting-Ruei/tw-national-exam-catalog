# -*- coding: utf-8 -*-
"""build_repair_drafts：deterministic 字形類草稿（workplan 4.1）的合約。

負控制：
  non-deterministic 的碼點（℃、Ⅰ）也被正規化      → 動到「紙本忠實形」的判斷（拒：一字不動）
  欄位在資料裡不存在仍寫草案                        → 憑空提案（拒：記 field-missing）
  重跑把草案行洗掉或重複追加                        → append-only 被破壞（拒：冪等）
  佇列被寫入                                        → 越界（拒：程式只開寫 --out）
"""
import json
import os
import sys
from pathlib import Path

HERE = os.path.dirname(os.path.abspath(__file__))
PKG = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(PKG, "scripts"))

from scripts import build_repair_drafts as builder  # noqa: E402


def fixture(tmp_path, *, stem, options=None, hits=None):
    row = {"candidate_key": "moex:t:q1", "question_number": 1,
           "stem": stem, "image_refs": [], "metadata": {},
           "options": [{"key": k, "text": t} for k, t in (options or [("A", "無")])]}
    candidates = tmp_path / "candidates.jsonl"
    candidates.write_text(json.dumps(row, ensure_ascii=False) + "\n", encoding="utf-8")
    rare = tmp_path / "rare-codepoint.jsonl"
    rare.write_text("".join(json.dumps(h, ensure_ascii=False) + "\n" for h in (hits or [])),
                    encoding="utf-8")
    return candidates, rare


HIT_STEM = {"candidate_key": "moex:t:q1", "lens": "rare-codepoint",
            "evidence": {"field": "stem"}}


def test_radical_only_normalization(tmp_path):
    """部首字形被換成標準字；非 deterministic 碼點（℃、Ⅰ）一字不動。"""
    candidates, rare = fixture(tmp_path, stem="⾜跟效應（℃）與第Ⅰ部", hits=[HIT_STEM])
    drafts, stats = builder.build(rare, candidates)
    assert len(drafts) == 1
    assert drafts[0]["fix"] == "足跟效應（℃）與第Ⅰ部", "℃ 與 Ⅰ 不在 deterministic 類——原樣保留"
    assert drafts[0]["insert"] == "stem"
    assert drafts[0]["changes"] == [{"from": "⾜", "u+": "U+2F9C", "to": "足", "class": "kangxi-radical"}]
    assert stats["kangxi-radical"] == 1


def test_option_field_and_missing_field(tmp_path):
    candidates, rare = fixture(tmp_path, stem="正常題幹",
                               options=[("A", "⽤於口服"), ("B", "無")],
                               hits=[{"candidate_key": "moex:t:q1", "lens": "rare-codepoint",
                                      "evidence": {"field": "option A"}},
                                     {"candidate_key": "moex:t:q1", "lens": "rare-codepoint",
                                      "evidence": {"field": "option C"}},
                                     {"candidate_key": "moex:t:absent", "lens": "rare-codepoint",
                                      "evidence": {"field": "stem"}}])
    drafts, stats = builder.build(rare, candidates)
    assert len(drafts) == 1 and drafts[0]["fix"] == "用於口服", "option C 不存在＝field-missing，不猜"
    assert stats["field-missing"] == 1
    assert stats["row-missing"] == 1, "帳上有、佇列沒有的鍵＝明講，不靜默"


def test_idempotent_append(tmp_path):
    candidates, rare = fixture(tmp_path, stem="⽤⽤無窮", hits=[HIT_STEM])
    out = tmp_path / "repair_drafts.jsonl"
    argv = ["--hits", str(rare), "--candidates", str(candidates), "--out", str(out)]
    assert builder.main(argv) == 0
    first = out.read_text(encoding="utf-8").strip().split("\n")
    assert len(first) == 1
    assert builder.main(argv) == 0
    second = out.read_text(encoding="utf-8").strip().split("\n")
    assert len(second) == 1, "重跑＝冪等（同 key,field,fix 不重複追加）"
    # 外人先追加的行要被保住：再跑一次之前，模擬另一輪寫了一行不同 fix。
    prior = json.loads(first[0])
    prior["fix"] = "別的修法"
    out.write_text(out.read_text(encoding="utf-8") + json.dumps(prior, ensure_ascii=False) + "\n",
                   encoding="utf-8")
    builder.main(argv)
    lines = out.read_text(encoding="utf-8").strip().split("\n")
    assert len(lines) == 2 and json.loads(lines[1])["fix"] == "別的修法", "append-only：別人的行不洗"


def test_provenance_shape(tmp_path):
    candidates, rare = fixture(tmp_path, stem="⽤", hits=[HIT_STEM])
    drafts, _ = builder.build(rare, candidates)
    row = drafts[0]
    assert row["schema"] == "repair_agent_test/repair_drafts v1"
    assert row["action"] == "repair_draft" and row["status"] == "proposed"
    assert row["session_id"] == "" and row["run_id"], "deterministic 產物：無 session、有 run 標記"
    assert row["prompt_version"] == "deterministic-radix-map-v1"
    assert row["at"].endswith("Z")


def test_main_without_out_prints_only(tmp_path, capsys):
    candidates, rare = fixture(tmp_path, stem="⽤", hits=[HIT_STEM])
    assert builder.main(["--hits", str(rare), "--candidates", str(candidates)]) == 0
    assert not (tmp_path / "repair_drafts.jsonl").exists(), "不給 --out 就不寫檔"
    assert "kangxi-radical" in capsys.readouterr().out