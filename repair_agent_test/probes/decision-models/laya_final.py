import warnings, time, json
import laya_mlx as laya
warnings.filterwarnings("ignore")
agent = laya.load("aac6fef/laya-multilingual-mlx")

# 全部改成 choice + 中性 key + 明確描述（避開 noul bug）
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
print(f"{'案例':<10} " + " ".join(f"{k:<10}" for k in Q))
tot=hit=0
for name,(st,want) in cases.items():
    r=agent.predict(st,Q)["answers"]
    row=[]
    for k in Q:
        got=r[k]["choice"]; ok = got==want[k]
        tot+=1; hit+=ok
        row.append(f"{got}{'✅' if ok else '❌'}")
    print(f"{name:<10} " + " ".join(f"{c:<10}" for c in row))
print(f"\n→ {hit}/{tot} = {hit/tot:.0%}  （對照：全猜 B 會得 5/20=25%）")

print("\n=== 全庫成本估算 ===")
qs={f"c{i}":{"type":"choice","instructions":"檢查項目","criteria":{"A":"是","B":"否"}} for i in range(20)}
t0=time.time(); agent.predict("下列何者正確？\n(A)甲\n(B)乙", qs); dt=time.time()-t0
print(f"20 個檢查一次呼叫：{dt*1000:.0f} ms")
print(f"79,090 題 × 20 檢查 = {79090*dt/60:.1f} 分鐘（單機）")
