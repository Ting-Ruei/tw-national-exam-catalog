import json, warnings, time
import laya_mlx as laya
warnings.filterwarnings("ignore")
agent = laya.load("aac6fef/laya-multilingual-mlx")

# 負對照：明確有圖 vs 明確沒圖，用同一組問句
cases = {
  "明確有圖": "根據下圖的劑量反應曲線，下列何者正確？\n(A) 曲線呈S形\n(B) 無反應",
  "明確無圖": "下列何者為心肌梗塞最典型的檢驗指標？\n(A) Troponin I\n(B) CRP\n(C) D-dimer\n(D) BNP",
  "明確有表": "下表為各藥物的半衰期，請問何者最長？\n(A) A藥\n(B) B藥",
  "有圖字但非圖": "關於圖形理論（graph theory）的敘述，下列何者正確？\n(A) 節點\n(B) 邊",
  "空白頁殘留": "請參閱附圖。",
}
q = {"has_figure":{"type":"noul","instructions":"這段考題文字是否要求考生看圖（圖片、圖表、照片、流程圖）才能作答？"}}
print(f"{'案例':<14} {'noul機率':>9}  {'判定':<6}")
for name, state in cases.items():
    r = agent.predict(state, q)["answers"]["has_figure"]
    p = r["noul"]; verdict = "有圖" if p>=0.5 else "無圖"
    print(f"{name:<14} {p:>9.4f}  {verdict:<6}")

print()
q2 = {"figure_kind":{"type":"choice","instructions":"這題的圖最可能是哪一種？",
      "criteria":{"化學結構":"分子結構式","統計圖":"折線圖、柱狀圖","示意圖":"解剖或流程圖","無圖":"沒有圖"}}}
print(f"{'案例':<14} {'choice':<10} {'信心':>7}")
for name, state in cases.items():
    r = agent.predict(state, q2)["answers"]["figure_kind"]
    print(f"{name:<14} {r['choice']:<10} {r['confidence']:>7.4f}")

# 批次速度
print("\n=== 批次速度（10 個問題一次呼叫）===")
qs = {f"q{i}":{"type":"noul","instructions":"這段文字是否包含數字？"} for i in range(10)}
t0=time.time(); agent.predict(cases["明確無圖"], qs); dt=(time.time()-t0)*1000
print(f"10 題一次呼叫：{dt:.0f} ms")
