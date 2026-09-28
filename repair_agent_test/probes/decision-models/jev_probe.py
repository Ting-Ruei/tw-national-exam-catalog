import warnings, time, json
warnings.filterwarnings("ignore")
from jev_style import JevStyle, noul

t0=time.time()
js = JevStyle.from_pretrained("chaoliangUNSW/Jev-Style-0.8B-Decision-v3-MLX", precision="8bit")
print(f"載入 {time.time()-t0:.1f}s")

# 與 Laya 完全相同的題目，才能直接比較
cases = {
  "明確有圖": ("根據下圖的劑量反應曲線，下列何者正確？\n(A) 曲線呈S形\n(B) 無反應", "是"),
  "明確無圖": ("下列何者為心肌梗塞最典型的檢驗指標？\n(A) Troponin I\n(B) CRP\n(C) D-dimer\n(D) BNP", "否"),
  "明確有表": ("下表為各藥物的半衰期，請問何者最長？\n(A) A藥\n(B) B藥", "是"),
  "有圖字但非圖": ("關於圖形理論（graph theory）的敘述，下列何者正確？\n(A) 節點\n(B) 邊", "否"),
  "空白頁殘留": ("請參閱附圖。", "是"),
}
q = {"need_figure": {"type":"choice", "instructions":"考生需要看圖片或圖表才能作答嗎？",
      "criteria":{"A":"是，需要看圖或圖表才能作答","B":"否，純文字即可作答"}}}
print("\n=== 任務：需不需要看圖（與 Laya 同題）===")
print(f"{'案例':<14} {'choice':<6} {'信心':>8}  正確?")
ok=0
for name,(st,want) in cases.items():
    r = js.decide(st, q)["answers"]["need_figure"]
    got = "是" if r["choice"]=="A" else "否"
    hit = got==want; ok+=hit
    print(f"{name:<14} {r['choice']:<6} {r.get('confidence',0):>8.4f}  {'✅' if hit else '❌'}")
print(f"→ {ok}/{len(cases)}   （Laya 多語版在此任務上方向全對但 noul 反向）")

# 客觀：選項數
q2={"n_opt":{"type":"choice","instructions":"這題總共有幾個作答選項？",
     "criteria":{"4":"四個選項","3":"三個選項","5":"五個選項","0":"沒有選項"}}}
cases2={"四選項":"下列何者正確？\n(A) 甲\n(B) 乙\n(C) 丙\n(D) 丁",
        "三選項":"下列何者正確？\n(A) 甲\n(B) 乙\n(C) 丙",
        "無選項":"請說明心肌梗塞的致病機轉。"}
print("\n=== 客觀：選項數（Laya 在此 1/5）===")
print(f"{'案例':<14} {'choice':<8} {'信心':>8}  正確?")
ok=0
for name,(st) in cases2.items():
    r=js.decide(st,q2)["answers"]["n_opt"]
    want={"四選項":"4","三選項":"3","無選項":"0"}[name]
    hit=r["choice"]==want; ok+=hit
    print(f"{name:<14} {r['choice']:<8} {r.get('confidence',0):>8.4f}  {'✅' if hit else '❌'}")
print(f"→ {ok}/3")

# 上下標（Laya 在此全錯）
q3={"has_sub":{"type":"choice","instructions":"這段文字含有下標或上標字元（如 CO₂、Ca²⁺）嗎？",
     "criteria":{"A":"有上下標字元","B":"沒有，只有普通文字"}}}
cases3={"有上下標":"血液中 Ca²⁺ 濃度過低時，PCO₂ 會如何變化？","無上下標":"血液中鈣離子濃度過低時，二氧化碳分壓會如何變化？"}
print("\n=== 客觀：上下標（Laya 在此全錯）===")
ok=0
for name,st in cases3.items():
    r=js.decide(st,q3)["answers"]["has_sub"]; want="A" if name=="有上下標" else "B"
    hit=r["choice"]==want; ok+=hit
    print(f"{name:<14} {r['choice']:<4} {r.get('confidence',0):>8.4f}  {'✅' if hit else '❌'}")
print(f"→ {ok}/2")

# 速度
st="下列何者正確？\n(A) 甲\n(B) 乙\n(C) 丙\n(D) 丁"
js.decide(st,q2)
ts=[]
for _ in range(10):
    t0=time.time(); js.decide(st,q2); ts.append((time.time()-t0)*1000)
ts.sort(); print(f"\n暖機後 10 次：中位數 {ts[5]:.0f} ms  最小 {ts[0]:.0f} ms")
