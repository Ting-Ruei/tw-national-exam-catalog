import time, json
import laya_mlx as laya

agent = laya.load("aac6fef/laya-mlx")
state = "Hi, we were billed twice for March. Please refund the duplicate today or we will cancel our plan."
q = {"department":{"type":"choice","instructions":"Which department?","criteria":["billing","technical","sales"]}}
agent.predict(state,q)  # warm
ts=[]
for i in range(10):
    t0=time.time(); r=agent.predict(state,q); ts.append((time.time()-t0)*1000)
ts.sort()
print("暖機後 10 次 ms:", [f"{t:.0f}" for t in ts])
print("中位數 %.0f ms  最小 %.0f" % (ts[5], ts[0]))

# 中文測試（英文 checkpoint 應該爛）
zh = "病患因急性心肌梗塞住院，經心導管檢查發現左前降支90%狹窄，需接受支架置入術。"
qz = {"診斷":{"type":"choice","instructions":"這題在描述哪一類疾病？",
             "criteria":{"心血管":"心臟與血管疾病","呼吸":"肺與呼吸道疾病","內分泌":"荷爾蒙與代謝"}}}
print("\n=== 英文 checkpoint 跑中文 ===")
print(json.dumps(agent.predict(zh,qz), ensure_ascii=False)[:600])
