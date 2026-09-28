# 判讀 agent — 怎麼啟動、怎麼管、怎麼用

**給設計者看的操作手冊。** 三件東西：

| 東西 | 是什麼 | 誰寫入 |
|---|---|---|
| **自進步 agent**（`agent.mjs`） | Pi 當指揮者，叫地端模型看紙本、找錯、判讀 | agent 寫 `store/agent_feedback.jsonl` |
| **判讀介面**（`ui/`） | 你一次看一題、右邊配 PDF、寫下你的判讀 | 你寫**同一條** `store/agent_feedback.jsonl` |
| **學習語料**（`store/lessons.jsonl`） | agent 自己累積的教訓，下一輪會讀到 | `read_page` 自動記 ＋ agent 主動記 |

**關鍵**：你寫的判讀和 agent 的判讀**進同一個檔**，所以 agent 下一輪的 `get_question`
就會看到你寫的話。這是整條迴路唯一的接點，也是 `learned=None` 缺陷修好的地方。

---

## 一、啟動

### 1. 判讀介面（你要用的）

```sh
cd "/Users/tim/AI workspace/ai_learning_platform/tw-national-exam-catalog/repair_agent_test/agent"
../../qbr/.venv/bin/python ui/server.py --port 8790
```

然後瀏覽器開 <http://127.0.0.1:8790>。

**先同步站上狀態**（協定：「站上是家」）：

```sh
cd ..
scripts/sync_from_station.sh --status    # 先看差多少
scripts/sync_from_station.sh             # 撈回最新
```

不先同步的話，你看到的題目是筆電上的舊快照，不是站上的權威版本。

### 2. agent（背景跑）

```sh
cd ".../repair_agent_test/agent"
node agent.mjs "看這一題並判讀：<candidate_key>"
```

換大腦（預設 `ornith-mtplx`）：

```sh
REPAIR_AGENT_MODEL="occamy-6bit/occamy-1.0-6bit-XL-mlx" node agent.mjs "…"
```

**agent 沒有 edit/write 工具**——它不能改題目、不能刪檔。它只能查、看紙本、寫判讀與教訓。
這是刻意的：治理上 G3（改題目）要你逐次核准，所以工具不給。

---

## 二、介面怎麼用

### 找一題

| 想做 | 怎麼做 |
|---|---|
| 知道 key | 貼進最上面 `candidate_key` 欄，按「讀這一題」 |
| 只知道題號 | 搜尋欄打 `68` 按 Enter |
| 找某個字 | 搜尋欄打題幹裡的字（例：`葡萄球菌`）按 Enter |
| 直接給別人看 | 開 `http://127.0.0.1:8790/?key=<candidate_key>` |

### 看什麼（左邊）

**一次一題、整題完整**（你第八輪的原話：「每一題都要讀取完整，不能只讀這個不讀那個」）：

- 題幹、共同題幹、A B C D（**答案那一個會變綠**）、答案
- **機器說法**：圖的歸屬、有沒有裁切、`ownership_note`
- **這個題目已有的判讀**：`source: designer` 是你寫的，`agent` 是模型寫的

選項是空的時候，畫面會提醒兩種可能：**選項本身是圖**（化學結構式等），或是**抽取缺陷**。
這正是你要看紙本分辨的地方。

### 看什麼（右邊）

**官方題目卷該頁**（不是答案卷），加上 **agent 送給模型看的裁片**。
右邊和左邊**完全解耦**：改左邊不會動右邊（v2 的既有規則）。

> ⚠️ **頁碼顯示 `未匯出` 是什麼意思**：`question_page` 是 A1 分支（`agent/export-question-page-20260927`）
> 才加的欄位，你現在跑的資料還沒重匯出過。PDF 面板仍會開在第 1 頁。
> 這是已知的，不是壞掉——要真正跳頁得先重跑匯出。

### 寫下你的判讀

底部文字框寫**依據**（不是「這題有問題」，是「哪個字／哪個欄位／哪張圖不同」），選 up/down，按「存檔」。

**存檔會拒絕兩件事**（刻意的）：
- 沒有 `candidate_key` → 拒絕（沒有題目的判讀沒人能處理）
- 依據是空白 → 拒絕（沒有依據的指導，模型學不到東西）

寫進 `store/agent_feedback.jsonl`，append-only。**永不碰 `question_review_events.jsonl`**
（那是人工審核紀錄，只有你能寫）。

---

## 三、怎麼知道 agent 學到了

### 看它讀到了什麼

```sh
# 你剛寫的那句話，agent 下一輪就會看到
../../qbr/.venv/bin/python bridge.py question --key "<key>" | python3 -c \
  "import json,sys; d=json.load(sys.stdin); print(len(d['prior_judgements']), '筆判讀')"
```

### 看它自己累積的教訓

```sh
cat store/lessons.jsonl | python3 -c "
import json,sys
for line in sys.stdin:
    r=json.loads(line)
    print('[%s] %s%s（%d 次）' % (r.get('subject') or '引擎', r['text'],
          '（依據：%s）' % r['evidence'] if r.get('evidence') else '', r.get('count',1)))
"
```

「次數」是重點：**同一個觀察在不同題重複出現，就是那個科目的習慣**。
`螢→熒` 這種是引擎層級（標 `[引擎]`），`這類題的圖常被切細縫` 是科目層級。

### 看 agent 做了什麼

```sh
python3 -c "
import json
for line in open('store/agent.log.jsonl'):
    r=json.loads(line)
    if r.get('event')=='tool_call': print('CALL', r['tool'])
    elif r.get('event')=='tool_result':
        s=r.get('summary') or {}
        print('  RES', r['tool'], r.get('ok'), s.get('error','')[:80])
"
```

**健康的樣子**：`get_question` → `read_page` → `record_judgement`，**沒有 `bash`**。
如果出現 `bash`，代表某個工具有問題，agent 在繞路自己找答案——那是要修的信號，不是它在亂搞。

---

## 四、管理

### 測試

```sh
node --test test_agent.mjs        # 10 個契約測試，每個都有負對照
```

### 停掉介面

`Ctrl-C`，或 `pkill -f "ui/server.py"`。

### 檔案在哪

| 路徑 | 內容 | 能不能刪 |
|---|---|---|
| `store/agent_feedback.jsonl` | **學習語料**（你的判讀 ＋ agent 的判讀） | **不要刪**，這是唯一會累積的東西 |
| `store/lessons.jsonl` | agent 的教訓 | 可清空重學 |
| `store/agent.log.jsonl` | 每輪軌跡 | 可刪（只是紀錄） |
| `store/crops/` | 裁片 | 可刪（會重裁） |

### 改題目？（**目前不行，要你核准**）

agent 只能判讀，不能改。要真的修題目（G3）現在還是手動。G4 已放寬（`agent_verified`
可交付，見 `qa-log.md` Q30），但**「交付」不等於「改了題目文字」**。

---

## 五、現在的限制（誠實清單）

1. **`question_page` 是 `None`**（A1 分支未合併／未重匯出），PDF 面板開在第 1 頁。
2. **一次一題**。分群（92.8% 快速瀏覽／7.2% 下指導）的**資料支持已有**，
   但介面上**還沒有**「一群一群看」的按鈕。
3. **只有一個模型看**。設計上支援兩個引擎（`occamy-6bit` ＋ `mtplx-35b` 第二意見），
   agent 會自己決定要不要叫第二個，但介面不會自動並排顯示兩個判讀。
4. **還沒有 G4 的自動流程**。`agent_verified` 這個狀態已經可用，
   但「agent 判乾淨 → 自動標 `agent_verified` → 包進可交付 package」這條線還沒接。
5. **藥師還沒掃**。

---

## 六、出問題時先看哪裡

| 症狀 | 先看 |
|---|---|
| 介面開啟但沒有題目 | `scripts/sync_from_station.sh --status`（是不是還沒同步） |
| 「找不到題目」 | candidate key 對不對；`bridge.py find --number 68` |
| PDF 面板空白 | 掃描頁 JPEG 2000；看 `qbr.review_ui.paths.safe_file_path` 的瀏覽器安全變體有沒有建 |
| agent 用 `bash` 繞路 | `store/agent.log.jsonl` 裡那個工具回什麼錯（**現在錯誤會寫進 log 了**） |
| agent 沒有引用你的判讀 | `bridge.py question` 有沒有 `prior_judgements`；沒有的話是 `bridge.py` 沒更新 |
| 判讀沒寫進去 | POST 回 400 通常是缺 `reason` 或 `key` |

**不要用「再疊一層」解決問題**（charter）。先看這張表，再看 `qa-log.md` 最新一則。
