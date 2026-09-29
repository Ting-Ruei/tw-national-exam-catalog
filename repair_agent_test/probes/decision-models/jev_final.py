import warnings, time, json
warnings.filterwarnings("ignore")
from jev_style import JevStyle
js = JevStyle.from_pretrained("chaoliangUNSW/Jev-Style-0.8B-Decision-v3-MLX", precision="8bit")

Q = {"sub_sup":{"type":"choice","instructions":"這題的文字裡有出現下標或上標字元嗎（例如 CO₂、Ca²⁺、Kₘ）？",
      "criteria":{"A":"有，出現上下標字元","B":"沒有，全部是普通大小的字"}},
     "opt_count":{"type":"choice","instructions":"這題總共有幾個作答選項？",
      "criteria":{"4":"四個選項","3":"三個選項","5":"五個選項","0":"沒有選項"}},
     "formula":{"type":"choice","instructions":"這題的內容需要計算嗎（有算式或數值運算）？",
      "criteria":{"A":"需要計算","B":"不需要計算"}},
     "fig_ref":{"type":"choice","instructions":"這題的文字有提到要看圖或看表嗎？",
      "criteria":{"A":"有提到圖或表","B":"沒提到"}},
}
cases = {
 "正常":       ("下列何者正確？\n(A) 甲\n(B) 乙\n(C) 丙\n(D) 丁",        {"sub_sup":"B","opt_count":"4","formula":"B","fig_ref":"B"}),
 "有上下標":    ("血液中 Ca²⁺ 與 PCO₂ 的關係？\n(A) 甲\n(B) 乙\n(C) 丙\n(D) 丁", {"sub_sup":"A","opt_count":"4","formula":"B","fig_ref":"B"}),
 "有圖":       ("根據下圖的劑量反應曲線，下列何者正確？\n(A) 甲\n(B) 乙\n(C) 丙\n(D) 丁", {"sub_sup":"B","opt_count":"4","formula":"B","fig_ref":"A"}),
 "要計算":     ("轉子半徑100 mm、轉速3,000 rpm，RCF 約為多少？\n(A) 1006×g\n(B) 906×g\n(C) 3006×g\n(D) 10006×g", {"sub_sup":"B","opt_count":"4","formula":"A","fig_ref":"B"}),
 "只有三選項":  ("下列何者正確？\n(A) 甲\n(B) 乙\n(C) 丙",              {"sub_sup":"B","opt_count":"3","formula":"B","fig_ref":"B"}),
}
print("=== Jev-Style 0.8B v3（MLX 8bit）同 20 檢查 ===")
print(f"{'案例':<10} " + " ".join(f"{k:<10}" for k in Q))
tot=hit=0
for name,(st,want) in cases.items():
    r=js.decide(st,Q)["answers"]
    row=[]
    for k in Q:
        got=r[k]["choice"]; ok=got==want[k]; tot+=1; hit+=ok
        row.append(f"{got}{'✅' if ok else '❌'}")
    print(f"{name:<10} " + " ".join(f"{c:<10}" for c in row))
print(f"\n→ {hit}/{tot} = {hit/tot:.0%}   (Laya 多語 9/20=45%, Laya 英 10/20=50%, 亂猜 25%)")

# 抽取忠實度任務
print("\n=== 抽取文字 vs 紙本是否忠實（我們的真正任務）===")
qf={"faithful":{"type":"choice","instructions":"這兩段文字是否完全相同（逐字比對，含標點與全形半形）？",
     "criteria":{"A":"完全相同","B":"有差異"}}}
pairs=[("完全相同","紙本：下列何者正確？  (A)甲 (B)乙","抽取：下列何者正確？  (A)甲 (B)乙","A"),
 ("完全不同","紙本：下列何者正確？  (A)甲 (B)乙","抽取：下列何者不正確？  (A)丙 (B)丁","B"),
 ("差在標點","紙本：下列何者正確？","抽取：下列何者正確.","B"),
 ("差一個字","紙本：抗癲癇藥物","抽取：抗癲癲藥物","B"),
 ("差全形半形","紙本：劑量１００ mg","抽取：劑量100 mg","B"),
 ("差在題號","紙本：30.病患出現","抽取：(30)病患出現","B")]
ok=0
for name,a,b,want in pairs:
    r=js.decide(f"{a}\n{b}",qf)["answers"]["faithful"]
    hit=r["choice"]==want; ok+=hit
    print(f"  {name:<12} {r['choice']:<4} {r.get('confidence',0):>7.4f}  {'✅' if hit else '❌'}")
print(f"→ {ok}/6  （Laya: 5/6，但它把「完全相同」誤判成 diff）")
