import warnings, json, time
import laya_mlx as laya
warnings.filterwarnings("ignore")
agent = laya.load("aac6fef/laya-multilingual-mlx")

# 任務一：抽取文字是否忠實於紙本（我們的核心判斷）
q = {"faithful":{"type":"choice","instructions":"這兩段文字是否完全相同（逐字比對，包含標點與全形半形）？",
     "criteria":{"same":"完全相同","diff":"有差異"}}}
pairs = [
 ("完全相同", "紙本：下列何者正確？  (A)甲 (B)乙", "抽取：下列何者正確？  (A)甲 (B)乙"),
 ("完全不同", "紙本：下列何者正確？  (A)甲 (B)乙", "抽取：下列何者不正確？  (A)丙 (B)丁"),
 ("差在標點", "紙本：下列何者正確？", "抽取：下列何者正確."),
 ("差一個字", "紙本：抗癲癇藥物", "抽取：抗癲癲藥物"),
 ("差全形半形", "紙本：劑量１００ mg", "抽取：劑量100 mg"),
 ("差在題號", "紙本：30.病患出現", "抽取：(30)病患出現"),
]
print("=== 任務一：抽取文字是否忠實 ===")
print(f"{'案例':<12} {'choice':<6} {'信心':>7}  正確?")
ok=0
for name, a, b in pairs:
    r = agent.predict(f"{a}\n{b}", q)["answers"]["faithful"]
    want = "same" if name=="完全相同" else "diff"
    hit = r["choice"]==want
    ok += hit
    print(f"{name:<12} {r['choice']:<6} {r['confidence']:>7.4f}  {'✅' if hit else '❌'}")
print(f"→ {ok}/{len(pairs)}")

# 任務二：格式缺陷的多選判斷（一次問多項）
q2 = {
 "sub_sup":{"type":"noul","instructions":"含有上下標字元嗎？"},
 "half_width":{"type":"choice","instructions":"數字是全形還是半形？","criteria":{"full":"全形１２３","half":"半形123","mixed":"混用"}},
 "option_count_ok":{"type":"noul","instructions":"選項數量是四個嗎？"},
}
cases = {
 "正常四選項半形":"下列何者正確？\n(A) 甲\n(B) 乙\n(C) 丙\n(D) 丁",
 "缺一個選項":"下列何者正確？\n(A) 甲\n(B) 乙\n(C) 丙",
 "有上下標":"血液中 Ca²⁺ 與 PCO₂ 的關係？\n(A) 甲\n(B) 乙",
 "全形數字":"劑量為１００毫克\n(A) 甲\n(B) 乙",
}
print("\n=== 任務二：格式缺陷 ===")
for name, st in cases.items():
    r = agent.predict(st, q2)["answers"]
    print(f"{name:<14} 上下標={r['sub_sup']['noul']:.2f}  數字={r['half_width']['choice']}({r['half_width']['confidence']:.2f})  四選項={r['option_count_ok']['noul']:.2f}")

# 任務三：信心可不可信（校正）
print("\n=== 任務三：同一問題重複 5 次，輸出是否穩定 ===")
st="下列何者正確？\n(A) 甲\n(B) 乙\n(C) 丙\n(D) 丁"
for i in range(3):
    r=agent.predict(st,q2)["answers"]
    print(f"  第{i+1}次 上下標={r['sub_sup']['noul']:.6f} 數字={r['half_width']['choice']} {r['half_width']['confidence']:.6f}")
