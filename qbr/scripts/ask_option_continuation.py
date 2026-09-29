#!/usr/bin/env python3
"""Ask a model the one question this project has shown models answer reliably.

Not "find the defects" - that was measured at 12-16% precision and rejected. This is a closed
question with a checkable answer about something visible, which is the use the project's own record
keeps coming back to: the two engines DISAGREE, and the model is asked which reading the paper
supports.

Ground truth exists for every item (the paper visibly continues), so the answers can be scored, and
the model is given text both engines produced rather than being asked to read the page itself.

Measured 2026-09-21, 10 cases from 8 sampled papers:
    qwen3.8-flash-next (DGX 8888)   10/10, and the recovered text matched ground truth verbatim
                                     apart from whitespace inserted around Latin terms
    ornith-1.5-mtplx-35b (18120)    10/10, same text, 2.4-11.2 s/question vs 1.1-2.9 s

Usage:
    .venv/bin/python scripts/ask_option_continuation.py --engine flash --papers 8 --limit 10
"""
"""The closed question about a picture: does the printed option continue past where we stopped?

This is the use the project has demonstrated the model is reliable at - not "find defects" (12-16%
precision) but a question with a checkable answer about something visible. The evidence is the two
engines' own readings, so the model is not asked to read the page; it is asked to adjudicate a
disagreement between two instruments using text that both produced.

Ground truth exists for every item here (the paper visibly continues), so an answer can be scored.
"""
import sys, os, json, argparse, urllib.request, time
sys.path.insert(0,"src")
from qbr import extract, repair, paths

ROOT=paths.repo_root()
ROOTS=["國考題資料夾","國考題資料夾_非醫學剩餘全集","國考題資料夾_其他類型"]
def pdf_for(p):
    rel=os.path.join("10_official_pdf","by_official_catalog",p["category"],str(p["year"]),"第%s次"%p["ordinal"])
    for r in ROOTS:
        d=os.path.join(ROOT,r,rel)
        if os.path.isdir(d):
            c=[f for f in os.listdir(d) if f.startswith(p["run"]) and f.endswith(".pdf") and "_ANS" not in f and "_MOD" not in f]
            if c: return os.path.join(d,sorted(c)[0])
    return None

PROMPT = """你看到的是同一份考卷的兩個抽取程式對第 {q} 題選項 {k} 的讀數。

程式 A（有字級，但折行會切斷句子）：
{rows_a}

程式 B（幾何精確，但每個記號單獨一列）：
{rows_b}

出貨的選項文字是：
{shipped}

問題（只有一個，請直接回答）：出貨的選項文字，是不是在句子還沒結束的地方就停了？
也就是說，兩個讀數是不是顯示同一句話在後面還有字，而出貨的文字沒有包含那些字？

先回答「是」或「否」，然後用一行寫出你認為被漏掉的字（若答否，寫「無」）。
不要解釋過程、不要推理其他可能性。格式：
判定：是/否
漏掉：...
"""

def ask(url, model, prompt, key, budget=2000, timeout=180, splash=False):
    body={"model":model,"messages":[{"role":"user","content":prompt}],
          "max_tokens":budget,"temperature":0}
    if not splash: body["reasoning_effort"]="none"
    req=urllib.request.Request(url.rstrip("/")+"/chat/completions",
        data=json.dumps(body).encode(),
        headers={"Content-Type":"application/json","Authorization":"Bearer "+key})
    t=time.time()
    with urllib.request.urlopen(req, timeout=timeout) as r:
        d=json.load(r)
    c=d["choices"][0]["message"]
    return (c.get("content") or ""), d.get("usage") or {}, time.time()-t

def parse(text):
    verdict=None; dropped=None
    for line in (text or "").splitlines():
        s=line.strip()
        if s.startswith("判定"): verdict="是" if "是" in s else ("否" if "否" in s else None)
        elif s.startswith("漏掉"): dropped=s.split("：",1)[-1].split(":",1)[-1].strip()
    return verdict, dropped

def main():
    ap=argparse.ArgumentParser()
    ap.add_argument("--papers",type=int,default=6)
    ap.add_argument("--seed",type=int,default=7)
    ap.add_argument("--engine",default="flash")
    ap.add_argument("--limit",type=int,default=8)
    a=ap.parse_args()
    Q=json.load(open("data/review-queues/live/review-ui/queue_index.json",encoding="utf-8"))
    import random; random.seed(a.seed)
    sample=random.sample(Q["per_paper"],a.papers)

    from qbr import continuation
    cases=[]
    for p in sample:
        pdf=pdf_for(p)
        if not pdf: continue
        ra=repair.mask_chrome(extract.extract_lines_a(pdf))[0]; rb=repair.mask_chrome(extract.extract_lines_b(pdf))[0]
        ta=repair.text_from_rows(ra); tb=repair.text_from_rows(rb)
        recs,_,_=repair.segment_best(ta)
        shipped={}
        for r in recs:
            n=r.get("number")
            if n is None: continue
            o=r.get("options") or {}
            if isinstance(o,list): o={x.get("key"):x.get("text") or "" for x in o if isinstance(x,dict)}
            shipped[n]={k:(v if isinstance(v,str) else (v or {}).get("text") or "") for k,v in o.items()}
        res=continuation.verify_paper(ta.split("\n"), tb.split("\n"), shipped)
        for item in res["both"]:
            if len(cases)>=a.limit: break
            n=item["question_number"]; k=item["option"]
            cases.append({"paper":p["run"],"q":n,"k":k,"shipped":shipped[n][k],
                          "dropped":item["dropped"],"ta":ta,"tb":tb,"ra":ra,"rb":rb})
        if len(cases)>=a.limit: break
    print("cases:",len(cases))
    if a.engine=="flash": url,model,key,sp="http://192.168.10.90:8888/v1","qwen3.8-flash-next","mtplx",False
    elif a.engine=="ornith": url,model,key,sp="http://127.0.0.1:18120/v1","ornith-1.5-mtplx-35b","mtplx",False
    else: url,model,key,sp="http://127.0.0.1:8088/v1","incoai/Qwen3.8-27B-Splash","",True
    ok=0; ans=[]
    for i,c in enumerate(cases):
        # show each reading around the option, so the model sees the disagreement not the whole page
        def around(text,n,k,width=3):
            ls=text.split("\n"); out=[]; hit=False
            for j,l in enumerate(ls):
                if l.strip().startswith("%d."%n): hit=True
                if hit: out.append(l)
                if len(out)>18: break
            return "\n".join(out)[:1400]
        pr=PROMPT.format(q=c["q"],k=c["k"],rows_a=around(c["ta"],c["q"],c["k"]),
                         rows_b=around(c["tb"],c["q"],c["k"]),shipped=c["shipped"])
        try: content,usage,dt=ask(url,model,pr,key)
        except Exception as e: content="ERROR %r"%e; usage={}; dt=0
        v,d=parse(content)
        hit = (v=="是")
        if hit: ok+=1
        ans.append({"paper":c["paper"][:30],"q":c["q"],"k":c["k"],"verdict":v,"dropped_model":d,
                    "dropped_true":c["dropped"],"sec":round(dt,1),"completion":(usage.get("completion_tokens"))})
        print(f"[{i+1}/{len(cases)}] {c['paper'][:26]} q{c['q']}{c['k']} → {v} | 真:{c['dropped'][:22]!r} | 模型:{str(d)[:22]!r} | {dt:.1f}s")
    print(f"\n=== {a.engine}: 判為『是』(即同意兩引擎) {ok}/{len(cases)} ===")
    json.dump(ans,open("/tmp/closedq_%s.json"%a.engine,"w"),ensure_ascii=False,indent=1)

main()
