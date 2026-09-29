import warnings
import laya_mlx as laya
warnings.filterwarnings("ignore")
agent = laya.load("aac6fef/laya-mlx")   # 英文 checkpoint

Q = {"sub_sup":{"type":"choice","instructions":"Does this text contain subscript or superscript characters (like CO2, Ca2+)?",
      "criteria":{"A":"yes, sub/superscripts present","B":"no, all characters normal size"}},
     "opt_count":{"type":"choice","instructions":"How many answer options does this question have?",
      "criteria":{"4":"four options","3":"three options","5":"five options","0":"no options"}},
     "formula":{"type":"choice","instructions":"Does answering this require a calculation?",
      "criteria":{"A":"yes, calculation needed","B":"no calculation"}},
     "fig_ref":{"type":"choice","instructions":"Does the text tell the student to look at a figure or table?",
      "criteria":{"A":"yes, mentions a figure or table","B":"no mention"}},
}
cases = {
 "normal":     ("Which of the following is correct?\n(A) a\n(B) b\n(C) c\n(D) d", {"sub_sup":"B","opt_count":"4","formula":"B","fig_ref":"B"}),
 "has_sub":    ("What is the relation between Ca2+ and PCO2?\n(A) a\n(B) b\n(C) c\n(D) d", {"sub_sup":"A","opt_count":"4","formula":"B","fig_ref":"B"}),
 "has_fig":    ("According to the dose-response curve below, which is correct?\n(A) a\n(B) b\n(C) c\n(D) d", {"sub_sup":"B","opt_count":"4","formula":"B","fig_ref":"A"}),
 "calc":       ("Rotor radius 100 mm at 3,000 rpm. What is the RCF?\n(A) 1006xg\n(B) 906xg\n(C) 3006xg\n(D) 10006xg", {"sub_sup":"B","opt_count":"4","formula":"A","fig_ref":"B"}),
 "three_opts": ("Which is correct?\n(A) a\n(B) b\n(C) c", {"sub_sup":"B","opt_count":"3","formula":"B","fig_ref":"B"}),
}
print("=== 英文 checkpoint 跑英文（同題目）===")
print(f"{'case':<11} " + " ".join(f"{k:<10}" for k in Q))
tot=hit=0
for name,(st,want) in cases.items():
    r=agent.predict(st,Q)["answers"]
    row=[]
    for k in Q:
        got=r[k]["choice"]; ok=got==want[k]; tot+=1; hit+=ok
        row.append(f"{got}{'✅' if ok else '❌'}")
    print(f"{name:<11} " + " ".join(f"{c:<10}" for c in row))
print(f"\n→ {hit}/{tot} = {hit/tot:.0%}  (always-B baseline = 25%)")
