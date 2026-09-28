import warnings, time; warnings.filterwarnings("ignore")
from jev_style import JevStyle, noul
js = JevStyle.from_pretrained("chaoliangUNSW/Jev-Style-0.8B-Decision-v3-MLX", precision="8bit")

cases = {
  "明確有圖": ("根據下圖的劑量反應曲線，下列何者正確？", True),
  "明確無圖": ("下列何者為心肌梗塞最典型的檢驗指標？(A) Troponin I (B) CRP", False),
  "明確有表": ("下表為各藥物的半衰期，請問何者最長？", True),
  "有圖字但非圖": ("關於圖形理論的敘述，下列何者正確？", False),
}
print("=== noul 型（Laya 在此完全反向）===")
print(f"{'案例':<14} {'noul':>8}  期望  正確?")
ok=0
for name,(st,want) in cases.items():
    r=js.decide(st, {"need_fig": noul("考生需要看圖片或圖表才能作答嗎？")})["answers"]["need_fig"]
    p=r["noul"]; got=p>=0.5; hit=got==want; ok+=hit
    print(f"{name:<14} {p:>8.4f}  {str(want):<5} {'✅' if hit else '❌'}")
print(f"→ {ok}/{len(cases)}   ← Laya noul 是 0/5 反向")

# 中文醫學語意（我們的主場）
print("\n=== 中文醫學：屬於哪一科（純標籤，無描述）===")
q={"科別":{"type":"choice","instructions":"這題屬於哪一科？","criteria":{"心臟內科":"","胸腔內科":"","內分泌科":""}}}
tests=[("病患因急性心肌梗塞住院，經心導管檢查發現左前降支90%狹窄。","心臟內科"),
       ("慢性阻塞性肺病患者出現呼吸困難與痰量增加。","胸腔內科"),
       ("第2型糖尿病患者糖化血色素控制不佳。","內分泌科")]
for st,want in tests:
    r=js.decide(st,q)["answers"]["科別"]
    print(f"  {r['choice']:<8} {r.get('confidence',0):>7.4f}  期望 {want}  {'✅' if r['choice']==want else '❌'}")

# 批次吞吐
t0=time.time()
for i in range(50): js.decide("下列何者正確？(A)甲(B)乙(C)丙(D)丁", {"a":{"type":"choice","instructions":"四個選項嗎？","criteria":{"A":"是","B":"否"}}})
dt=time.time()-t0
print(f"\n50 次呼叫：{dt:.1f}s → {dt/50*1000:.0f} ms/次；全庫 79,090 題 = {79090*dt/50/60:.0f} 分鐘")
