import warnings, time
import laya_mlx as laya
warnings.filterwarnings("ignore")
agent = laya.load("aac6fef/laya-multilingual-mlx")

cases = {
  "明確有圖": "根據下圖的劑量反應曲線，下列何者正確？\n(A) 曲線呈S形\n(B) 無反應",
  "明確無圖": "下列何者為心肌梗塞最典型的檢驗指標？\n(A) Troponin I\n(B) CRP\n(C) D-dimer\n(D) BNP",
  "明確有表": "下表為各藥物的半衰期，請問何者最長？\n(A) A藥\n(B) B藥",
  "有圖字但非圖": "關於圖形理論（graph theory）的敘述，下列何者正確？\n(A) 節點\n(B) 邊",
  "空白頁殘留": "請參閱附圖。",
}
# 官方建議解法：用兩選項 choice，中性 key + 描述寫明
q = {"need_figure":{"type":"choice","instructions":"考生需要看圖片或圖表才能作答嗎？",
     "criteria":{"A":"是，需要看圖或圖表才能作答","B":"否，純文字即可作答"}}}
print("=== 解法：choice（中性 key）取代 noul ===")
print(f"{'案例':<14} {'choice':<4} {'信心':>7}  {'機率(是)':>8}")
for name, state in cases.items():
    r = agent.predict(state, q)["answers"]["need_figure"]
    print(f"{name:<14} {r['choice']:<4} {r['confidence']:>7.4f}  {r['probabilities']['A']:>8.4f}")

print()
# 直接問選項數（這是有客觀答案的）
q2={"n_options":{"type":"choice","instructions":"這段題目提供了幾個選項（A/B/C/D 這種）？",
     "criteria":{"3":"三個","4":"四個","5":"五個","none":"沒有選項"}}}
cases2={
 "四選項":"下列何者正確？\n(A) 甲\n(B) 乙\n(C) 丙\n(D) 丁",
 "三選項":"下列何者正確？\n(A) 甲\n(B) 乙\n(C) 丙",
 "無選項":"請說明心肌梗塞的致病機轉。",
}
print("=== 客觀問題：選項數 ===")
print(f"{'案例':<14} {'choice':<6} {'信心':>7}")
for name, state in cases2.items():
    r = agent.predict(state, q2)["answers"]["n_options"]
    print(f"{name:<14} {r['choice']:<6} {r['confidence']:>7.4f}")

print()
# 客觀問題：題幹是否含全形/半形混用、含上下標字元
q3={"has_sub":{"type":"choice","instructions":"這段文字含有下標或上標字元（如 CO₂、Ca²⁺）嗎？",
     "criteria":{"A":"有上下標字元","B":"沒有，只有普通文字"}}}
cases3={
 "有上下標":"血液中 Ca²⁺ 濃度過低時，PCO₂ 會如何變化？",
 "無上下標":"血液中鈣離子濃度過低時，二氧化碳分壓會如何變化？",
 "有希臘字母":"計算 δ 值與 σ 值。",
}
print("=== 客觀問題：是否有上下標 ===")
print(f"{'案例':<14} {'choice':<4} {'信心':>7}")
for name, state in cases3.items():
    r = agent.predict(state, q3)["answers"]["has_sub"]
    print(f"{name:<14} {r['choice']:<4} {r['confidence']:>7.4f}")
