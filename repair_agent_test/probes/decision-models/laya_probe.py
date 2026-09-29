import time, json
import laya_mlx as laya

t0=time.time()
agent = laya.load("aac6fef/laya-mlx")
t_load=time.time()-t0
print(f"載入秒數 {t_load:.1f}")

state = "Hi, we were billed twice for March. Please refund the duplicate today or we will cancel our plan."
questions = {
    "department": {"type":"choice","instructions":"Which department should handle this request?",
                   "criteria":["billing","technical","sales"]},
    "refund": {"type":"noul","instructions":"Does the customer ask for money back?"},
    "urgency": {"type":"score","instructions":"How urgent is this?","criteria":["not urgent","soon","blocking"]},
}
t0=time.time()
res = agent.predict(state, questions)
t_pred=time.time()-t0
print(f"推論秒數 {t_pred*1000:.0f} ms")
print(json.dumps(res, ensure_ascii=False, indent=2)[:1500])
