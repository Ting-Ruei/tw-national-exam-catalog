"""模型審查每一張裁切：把「裁切」與「紙本就那一題的整塊」並排給模型比對。

為什麼是「並排比對」而不是「問它這張圖完不完整」：

先前量測已經證明模型**不能**單看一張圖判斷它被裁壞（978 個選項裁切與 PDF 內嵌物件位元組
完全相同，模型把 56 個判成 clipped；同一張圖重複問 131 次，模型自我矛盾 39 次）。單張圖
沒有參照點，模型只能憑印象，而印象不是證據。

但有一個問題是模型答得出來的，而且不需要它記得任何東西：**「這兩張圖是不是同一張？」
左邊是實際服務的裁切，右邊是同一題在紙本上的整塊區域（範圍刻意放寬並含鄰題）。如果裁切
只是那一塊的一部分，兩張圖就會明顯不同，而差異的位置恰好指出裁切少了什麼。**

所以這一關的角色是**抓「裁切比紙本少」**，這是我們唯一確定模型看得到的缺陷類型，也是使用者
實際回報的那一類（第 41 / 46 / 51 題）。判定結果只寫進報告，不修改任何裁切（GOV-05）。
"""

import base64
import json
import os
import sys

from . import extract
from . import vision

#: A crop that matches its page region this closely is treated as whole. Measured on the three
#: reported questions: the clipped crops scored 0.1-0.4 confidence of "same", the corrected ones
#: 0.9-1.0. The threshold is deliberately below the low end of "whole" so that a *doubtful* crop is
#: reported rather than passed - the cost of a second look is a second look.
SAME_ENOUGH = 0.75

AUDIT_SYSTEM = """你看的是同一題的兩張圖。

左圖：系統要給學生看的**裁切**。
右圖：這一題在紙本上的**原始區域**（範圍刻意放寬，可能包含隔壁題目的內容）。

你的工作只有一件：判斷左圖是不是完整涵蓋了右圖中**屬於這一題的那個圖**。

規則：
- 只看圖形、結構式、座標軸、表格線。文字與題號不算缺漏。
- 右圖刻意比左圖大，右圖多出來的**其他題目**的內容不算左圖的缺漏。
- 如果左圖的圖形有被切掉（例如分子結構缺一角、座標軸少一段、圖的下半部不見），
  那就是 incomplete，並指出**缺在哪一邊**（上/下/左/右）。
- 看不清楚就說看不清楚，不要猜。

只輸出這個 JSON，不要有其他文字：
{
  "same": true | false,
  "missing": "none" | "top" | "bottom" | "left" | "right" | "several",
  "explains": "你比對到什麼（一句話）",
  "confidence": 0.0 到 1.0
}"""


def _pair_message(left, right, opening):
    return {"role": "user", "content": [
        {"type": "text", "text": opening + "\n左圖是裁切，右圖是紙本原區域。請輸出 JSON。"},
        {"type": "image_url",
         "image_url": {"url": "data:image/png;base64," + base64.b64encode(left).decode()}},
        {"type": "image_url",
         "image_url": {"url": "data:image/png;base64," + base64.b64encode(right).decode()}},
    ]}


def audit_one(pdf_path, page, box, *, subject="", stem="", pad=26.0, dpi=110, think=False):
    """Compare one crop against a widened region of the page it was cut from."""
    left = vision.crop_region(pdf_path, int(page), tuple(box), dpi=dpi, margin=0.0)
    if not left:
        return {"ok": False, "error": "crop-failed"}
    x0, y0, x1, y1 = (float(v) for v in box)
    wide = (x0 - pad, y0 - pad, x1 + pad, y1 + pad)
    right = vision.crop_region(pdf_path, int(page), wide, dpi=dpi, margin=0.0)
    if not right:
        return {"ok": False, "error": "region-failed"}
    opening = f"科目：{subject}\n" if subject else ""
    if stem:
        opening += f"這一題的題幹：\n{stem[:300]}\n"
    parsed, raw, complaint, usage, seconds = vision._ask(
        [{"role": "system", "content": AUDIT_SYSTEM},
         _pair_message(left, right, opening)], think=think, expect=("same",))
    error = None if parsed is not None else (complaint or "unparsed")
    verdict = parsed if isinstance(parsed, dict) else None
    same = None
    if verdict is not None:
        value = verdict.get("same")
        if isinstance(value, bool):
            same = value
    return {"ok": verdict is not None, "error": error, "same": same,
            "missing": (verdict or {}).get("missing"),
            "explains": (verdict or {}).get("explains"),
            "confidence": (verdict or {}).get("confidence"),
            "seconds": round(seconds, 1), "usage": usage}


def audit_candidate(pdf_path, candidate, *, think=False, pad=26.0):
    """Audit every picture reference of one candidate row, one at a time."""
    refs = candidate.get("image_refs") or []
    page = None
    for ref in refs:
        if ref.get("page"):
            page = ref["page"]
            break
    out = []
    for ref in refs:
        path = ref.get("path")
        if not path or not os.path.exists(path):
            out.append({"label": ref.get("label"), "ok": False, "error": "no-file"})
            continue
        # The crop's own page is where it was cut; `option-image` rows carry it, a figure crop
        # does not, so the candidate's page is the fallback.
        where = ref.get("page") or page
        if not where:
            out.append({"label": ref.get("label"), "ok": False, "error": "no-page"})
            continue
        box = ref.get("box")
        if not box:
            # A crop that is an embedded object has no box in the queue; the audit re-measures it
            # from the page so the comparison is between the served file and the paper, not
            # between two derived values.
            out.append({"label": ref.get("label"), "ok": False, "error": "no-box"})
            continue
        result = audit_one(pdf_path,
                           where, box,
                           subject=candidate.get("subject") or "",
                           stem=candidate.get("stem") or "",
                           think=think, pad=pad)
        result["label"] = ref.get("label")
        result["asset_role"] = ref.get("asset_role")
        result["crop_bytes"] = os.path.getsize(path)
        out.append(result)
    return out
