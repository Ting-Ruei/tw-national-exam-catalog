import time, json, warnings
import laya_mlx as laya
warnings.filterwarnings("ignore")

t0=time.time()
agent = laya.load("aac6fef/laya-multilingual-mlx")
print(f"多語版載入秒數 {time.time()-t0:.1f}")

zh = "病患因急性心肌梗塞住院，經心導管檢查發現左前降支90%狹窄，需接受支架置入術。"
qz = {"診斷":{"type":"choice","instructions":"這題在描述哪一類疾病？",
             "criteria":{"心血管":"心臟與血管疾病","呼吸":"肺與呼吸道疾病","內分泌":"荷爾蒙與代謝"}}}
print("\n=== 多語版跑中文（有給描述）===")
r=agent.predict(zh,qz); print(json.dumps(r,ensure_ascii=False,indent=1)[:700])

# 真實考題式測試：格式判斷
state2 = """下列何者為心肌梗塞最典型的檢驗指標？
(A) Troponin I
(B) CRP
(C) D-dimer
(D) BNP"""
q2 = {"has_formula":{"type":"noul","instructions":"這段文字含有數學公式或化學式嗎？"},
      "has_figure_ref":{"type":"noul","instructions":"這段文字提到圖表（如「如圖所示」「下表」）嗎？"},
      "qtype":{"type":"choice","instructions":"這是哪一種題型？","criteria":{"單選":"只有一個正確答案","複選":"需要選多個","題組":"多小題共用題幹"}}}
print("\n=== 多語版跑「考題格式判斷」（這是我們真正要它做的）===")
r2=agent.predict(state2,q2); print(json.dumps(r2,ensure_ascii=False,indent=1)[:900])

# 中文無描述（純標籤）
q3={"科別":{"type":"choice","instructions":"屬於哪一科？","criteria":["心臟內科","胸腔內科","內分泌科"]}}
print("\n=== 純標籤（無描述）===")
print(json.dumps(agent.predict(zh,q3),ensure_ascii=False)[:500])
