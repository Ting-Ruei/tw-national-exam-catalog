---
name: deploy-qbr-review
description: Run the new-pipeline question Review UI as a container on the LAN — start it at boot, open it from another device's browser, verify it is actually serving the right queue, and pull evidence from that machine when something looks wrong. Use when setting up or restarting the review server, when another device cannot reach the review UI, when the interface shows the wrong question count, or when deciding which queue a container is serving.
---

# Deploying the Review UI (new pipeline, LAN)

`tw-national-exam-catalog/deploy/qbr-review/`. It serves the **qbr (new) pipeline's** queue over
HTTP so a reviewer can open it from a browser on another device.

**What this is not:** any historical SQL-backed deployment copy. The qbr review service is
JSONL-based and its current contract is defined here and in `review_ui/AGENTS.md`.
Those are a **different review store**. Reviewing the new pipeline and mixing the old one in is the
error this deployment exists to avoid (`qbr/reports/two_review_stores.md`).

## Where it runs, and why that matters more than how

The review UI lives on the **always-on station**, not on the laptop:

| | |
|---|---|
| **Station (the home)** | `192.168.10.70` = `TimsMac.lan`, M4 Max, up 70 days, Docker Desktop `AutoStart=true` |
| **The URL** | **`http://192.168.10.70:8765/v2`** |
| Laptop | may be closed and carried away — it is a **consumer**, not the host |

The station is where the old SQL review UI used to run (`tw-national-exam-review-ui`, `Exited 137`,
`--review-backend sql` → Postgres on 8765/8766). It was **neutralised with `docker update
--restart=no`** and kept for archaeology; it is not deleted because it is evidence of the old store.

**The rule this topology exists to keep: one review store.** Because the laptop can be closed, the
human decisions must permanently live on the station; otherwise a reviewer working while the laptop
is away writes into a second store and the two silently diverge. Concretely:

- The station's container is **the only writer**. Do **not** leave a Review UI running on the laptop
  on a second port — two live writers *are* two stores.
- The laptop **pulls** the decisions back for packages, reports and backup:
  `scripts/pull_station_reviews.sh` (one-way station → laptop, backs up before overwriting).

### The station's layout (self-contained on purpose)

The station does **not** serve out of a git checkout. Everything it needs is under `~/qbr-review/`,
so the service does not depend on any checkout still being there or being up to date:

```
~/qbr-review/
  code/     the repo minus 國考題資料夾*, qbr/data, .git, .venv     (55M, rsync'd)
  queue/    candidates.jsonl + crops + question_review_events.jsonl  (1.0G)
  assets/   國考題資料夾/10_official_pdf/by_official_catalog only    (1.9G, 30 categories)
  bin/      ensure-up.sh           (watchdog)
  logs/     ensure-up.log
```

The corpus is **only** `by_official_catalog` (1.9G of the laptop's 20G): that is the only subtree
the queue's `question_pdf_relative` refers to, and it is verified **byte-identical** to the laptop
(all 1,960 PDFs the queue needs, sha256 equal). The rest is for rebuilding, which the station
never does.

Config lives in `~/qbr-review/code/deploy/qbr-review/.env` (not in git): `REVIEW_UI_PORT=8765`,
`CATALOG_ROOT`, `ASSET_ROOT`, `QBR_QUEUE_DIR`, `QBR_REVIEW_LOG`, all absolute.

> **8765 is the contract.** It is the old review UI's number and the reviewer's bookmarks/PWA point
> at it. Moving the service between machines must not move the URL.

## Start it

```sh
cd tw-national-exam-catalog
deploy/qbr-review/up.sh              # build image, prepare queue, start, verify by itself
deploy/qbr-review/up.sh --rebuild    # re-run the queue from packages first (after a code change)
```

Then, from any device on the LAN:

```
http://<this-machine-LAN-IP>:8765/v2
```

At boot: Docker Desktop **AutoStart = True**, container `qbr-review-ui` with
`restart: unless-stopped`. That covers "the machine rebooted". It does **not** cover "the container
was stopped by hand" or "Docker came up before the container existed", so the station also has a
watchdog — see *Keeping it up* below.

## The three things up.sh checks, and why in that order

1. **The queue exists and is not empty.** A 0-question queue opens an interface that *looks*
   broken; "the interface is broken" and "the queue was never built" are different problems, so the
   script separates them and says which one it is.
2. **The review log file exists.** It is the one writable thing. A Docker bind mount pointed at a
   missing path creates a **directory**, and then the server fails opening it as a file. `up.sh`
   touches it first.
3. **The service is actually serving the queue.** It calls the API and compares the served question
   count against `wc -l candidates.jsonl`. Mismatch → non-zero exit.

> **"Started" is not "drawing".** A running container is not evidence the reviewer can see a
> question. `up.sh` measures the **API's number**, not the container's state.

## The write path — why the queue is read-only and the log is not

| Mount | Mode | Why |
|---|---|---|
| catalog `/workspace` | ro | an audit container must not be able to change the pipeline |
| PDFs `/workspace/國考題資料夾` | ro | read the paper, never write it |
| **queue root `/queue`** | ro | `candidates.jsonl`/crops are **rebuildable** |
| **log `/queue/review-ui/question_review_events.jsonl`** | **rw** | human decisions are **irreplaceable** |

Docker cannot mount "a directory read-only, one file inside it read-write", so the log is a separate
single-file bind nested inside the read-only queue dir. This makes "a rebuild cannot lose a human
record" a **mount**, not a discipline.

`REVIEW_UI_ADDITIONAL_ASSET_ROOTS=/queue` because the queue is **self-contained**: crop references
are stored **relative to the queue root** (`build_review_queue.py::_adopt_crops`), so a crop
resolves no matter where the queue is mounted. The bug this fixed: refs used to be relative to the
**build-time working directory**, so every image 404'd inside a container.

## Traps (each cost a real outage)

- **Relative paths in `compose.yaml` resolve against the compose file's directory, not CWD.**
  `up.sh` therefore resolves `QBR_QUEUE_DIR`/`QBR_REVIEW_LOG` to **absolute** paths and exports
  them. Otherwise the script checks one path while compose mounts another — a false pass.
- **The image ENTRYPOINT is already `python3`.** `command:` must start with the **script name**, not
  another `python3`. Written twice, the container restarts forever with
  `can't open file '/workspace/python3'`.
- **Brace every variable expansion** in shell (`${VAR}`). A full-width character immediately after
  `$VAR` gets absorbed into the name; `up.sh` died on exactly this.
- **Ports are parameters.** 8774 is the number the new pipeline has always used and **bookmarks and
  an installed PWA are a contract** — the default must not change. Use `.env` to move it.
- **`--review-backend jsonl` needs no `DATABASE_URL`.** Deliberately not set, so the next reader does
  not think the two routes are one.

## Keeping it up (the station's watchdog)

Docker Desktop `AutoStart=true` + `restart: unless-stopped` covers the reboot case only. The gap:
**a container stopped by hand, or Docker coming up before the container exists, has nobody to bring
it back.** The station closes that gap with a launchd job and an idempotent script:

```
~/Library/LaunchAgents/com.timsvms.qbr-review-ensure.plist   RunAtLoad + every 300s
~/qbr-review/bin/ensure-up.sh                                 probes the API; repairs only if down
~/qbr-review/logs/ensure-up.log                               empty when healthy
```

Both files live in the repo (`deploy/qbr-review/`), because a plist that only exists on one machine
is a machine that cannot be rebuilt. Install them with:

```sh
deploy/qbr-review/install-watchdog.sh              # idempotent: bootout then bootstrap
QBR_HOME=/some/other/path deploy/qbr-review/install-watchdog.sh   # non-default location
```

It is idempotent (re-running does not stack jobs), rewrites the paths when `QBR_HOME` differs, and
**verifies the job appears in `launchctl list`** — "bootstrap returned no error" is not "the job
exists".

It measures the **API's number**, not the container's state — same rule as `up.sh`. When the service
is healthy it writes nothing, so an empty log is the correct reading.

### Two bugs the watchdog had, and one outage I caused (2026-09-21)

Both were found by actually stopping things, not by reading the script:

1. **`open -ga Docker` does not start Docker Desktop on this machine.** The app is **nested**:
   `/Applications/Docker.app/Contents/MacOS/Docker Desktop.app`. `open -ga Docker` returns 0 and does
   **nothing**, so the watchdog "tried to start Docker" every 5 minutes and failed silently.
   Fixed to use the explicit nested bundle path (with the name-based call only as a fallback).
2. **A process name is not a health check.** The main process is `Docker Desktop`, not `Docker`, so
   `pgrep -x Docker` reports "not running" while Docker is fine. Only `docker info` is trustworthy.

> **Do not `osascript quit` Docker Desktop to "simulate a reboot".** This host is also the
> always-on exam platform (`exam_edge`, `exam_db`, `ai_learning_platform-*` all run here). I did
> exactly that to test the watchdog, its VM wedged on `no route to host`, and **the whole platform
> was down for ~30 minutes** before it recovered. To exercise the watchdog, stop **only the
> container** (`docker stop qbr-review-ui`) — that is the gap it exists to cover. The watchdog's
> Docker-start branch is for a Docker that is genuinely not running, not a test fixture.

> Note the station has **no auto-login**; its `console` user is `tim`, logged in for 70 days, and
the exam platform already depends on the same Docker Desktop AutoStart. The review UI matches that
existing reliability model rather than inventing a different one.

## The one review store (what the reviewer's decisions are, and where they live)

`~/qbr-review/queue/review-ui/question_review_events.jsonl` on the station is **the home**. It is
the only thing here that cannot be rebuilt: the queue is rebuildable from packages, the crops from
PDFs, the corpus from the archive — **a person's decision is not**.

Pull it back to the laptop before building packages, writing reports, or backing up:

```sh
scripts/pull_station_reviews.sh            # one-way station → laptop
scripts/pull_station_reviews.sh --dry-run  # say what would happen
```

It backs up the laptop's copy (`.pre-pull-<stamp>.jsonl`) before overwriting, and refuses to act at
all when the station is unreachable — *rather than overwrite a human record with a file whose
provenance is unknown*.

### The other direction: inspect on the laptop, push the correction back

The station is the home, but the loop the reviewer actually wants is *pull a copy, fix it, push the
new version back*. That direction exists, and it is not `scp`:

```sh
scripts/push_reviews_to_station.sh            # append-only: station ← laptop
scripts/push_reviews_to_station.sh --dry-run  # say how many events would be appended
```

**It only ever appends the events the station does not already have.** Someone may have kept
reviewing on the station (a lab computer does exactly this) while the laptop held a copy, and
transferring the file over would delete their work silently. The server's own write is `open("a")`
(`qbr/review_ui/review_state.py`, the append-only writers) and it reloads when the file signature changes (`qbr/review_ui/events.py::file_signature`), so
appending to a running server is what it already supports — no restart, no second writer.

It refuses to act when the station is unreachable, and when the station's record has *fewer* lines
than the laptop's (the home should never be the smaller one — that means something was deleted and
is worth understanding before writing anything). It backs up the station's log to
`~/qbr-review/backups/question_review_events.pre-push-<stamp>.jsonl` **before** appending, on the
station rather than the laptop, so the backup survives a failure of the laptop's connection.

**Event identity is content, not bytes.** The queue builder adds `_carried_from` when it carries a
record forward, so the same decision is a *different string* in two queues. Comparing whole lines is
therefore wrong, and measured wrong: station 347 lines, laptop 346 lines, and whole-line comparison
said "append 151 lines" — re-writing 151 decisions a person had already made. The rule is
`review_queue.record_identity` (the record's content minus `_carried_from`) and it is written
**once**; `merge_events.py` uses it, and so does `build_review_queue.py`. A hand-rolled second
definition in the shell is how a review record goes missing, which is why `test_merge_events.py`
asserts both directions of the mistake (an empty exclusion set → 2 failures; keying on
`candidate_key` → 1 failure).

## When something looks wrong — pull evidence from that machine

Do not reason from the other device's screen. On the station (`ssh 192.168.10.70`):

```sh
cd ~/qbr-review/code

# Is it up, and what is it serving?
docker compose -f deploy/qbr-review/compose.yaml ps
curl -s http://127.0.0.1:8765/api/queue_index | python3 -m json.tool | head -20

# What does the container itself say?
docker compose -f deploy/qbr-review/compose.yaml logs --tail 80

# Which queue is mounted, and how many records are in it?
docker inspect qbr-review-ui --format '{{range .Mounts}}{{.Source}} -> {{.Destination}} ({{.Mode}})
{{end}}'
wc -l ~/qbr-review/queue/review-ui/question_review_events.jsonl

# Is an image actually served (not just the page)?
curl -s -o /dev/null -w '%{http_code} %{content_type}\n' \
     "http://127.0.0.1:8765/file?path=review-ui/crops/<paper>/<name>.png"
```

The asset route is **`/file?path=<urlencoded queue-relative path>`**, not `/queue/...`: the queue
root is an *additional asset root* and refs are resolved against it.

The question to answer first is always: **is it not drawing, or is the thing not there?**
(`build-exam-question-bank/SKILL.md` records the same lesson: the first three explanations were
about rendering; the cause was that the scan never saw the picture.)

## The report step was O(n²), and that stalled the loop (2026-09-24)

`qbr/scripts/report_repair_progress.py` is step ③ of every round, and it was taking **27.8 s** —
enough that a round looked hung (a round is ~59 s total, and the process tree showed it sitting in
this step for minutes under contention).

`cProfile` put **20.8 s of 22.6 s inside `summarize`'s loop**, while parsing the whole 500 MB stream
was 0.96 s. The cause was the per-class dedup: `if key not in entry["questions"]`, where
`questions` grows to **81,151 keys** in the biggest class — a linear scan per record, i.e. O(n²).
Replaced with a per-class `set` (dropped again before returning, so the summary's shape is
unchanged): **27.8 s → 1.7 s**, with byte-identical output.

Two traps on the way, both worth keeping:

- **The obvious "fix" was slower.** Streaming the file backwards in 1 MB chunks to avoid
  materialising it measured **17.1 s vs 1.49 s** — an 11× regression — because back-to-front
  decoding allocates a new bytes object per chunk while the forward read is one linear pass with OS
  readahead. Memory was never the binding constraint. The real fix was finding the O(n²), not
  avoiding the allocation.
- **"Newest first" is a property of the walk, not a reversal of the result.** A question appearing
  in several records is kept at its **first sighting**, so reversing each class's list is *not* the
  same as reversing the records. Measured: class `verdict:DEFECT` led with `q002` (records reversed)
  vs `q053` (lists reversed), both holding 155 questions. `load_findings` now reverses once, at the
  end, and `summarize` consumes newest-first.

The read is bounded at `max_records=200_000` and returns `(rows, capped)`; `main` prints
「紀錄很長，只讀最新 N 筆」 when it cuts, so a capped summary never reads as a total.

## The repair loop runs **on the station** (2026-09-24)

The loop (scan → read the paper → orchestrator triage) and the browser UI must be **the same
machine**, because the progress panel reads the loop's own files. Until 2026-09-24 the loop ran only
on the laptop, so the station's 錯題討論區 showed "排隊中 0 題" while a laptop somewhere was working
— the panel was honest and useless.

```
~/Library/LaunchAgents/com.qbr.repair-daemon.station.plist   RunAtLoad + every 600s ← the loaded one
~/Library/LaunchAgents/com.qbr.repair-daemon.plist   the retired laptop copy; NOT loaded (see below)
~/qbr-review/code/qbr/scripts/com.qbr.repair-daemon.station.plist   (in the repo - a plist that
                                                                     exists on one machine is a
                                                                     machine that cannot be rebuilt)
~/qbr-review/logs/repair-daemon.{out,err}.log        launchd's view
~/qbr-review/code/qbr/runs/repair_daemon-*.log       what each round did
```

**There is exactly one such job, and it is this one.** The laptop's was unloaded on 2026-09-24
because two loops maintain two `scan_state.json` ledgers, and a queue sync pushes the laptop's over
the station's while the laptop's findings stream is protected (never pushed): the station would then
treat the laptop's questions as already read and never read them, without printing anything.
Measured 0 such questions on the day; the mechanism is the reason, not an incident. The laptop's
plist was deleted from the repo the same day — a plist nobody loads is a trap for the next person
who installs the wrong one.

Install on the station:

```sh
launchctl bootout gui/$(id -u)/com.qbr.repair-daemon 2>/dev/null || true
cp ~/qbr-review/code/qbr/scripts/com.qbr.repair-daemon.station.plist ~/Library/LaunchAgents/
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.qbr.repair-daemon.station.plist
launchctl print gui/$(id -u)/com.qbr.repair-daemon   # verify it EXISTS, not that bootstrap was quiet
```

Four things the station shape needs, each measured:

- **`QBR_PYTHON=/usr/bin/python3`.** The station has no `qbr/.venv`; its system Python 3.9 already
  carries PyMuPDF and Pillow, which is everything the loop imports. Verified by running `--help` on
  all three scripts there.
- **`ORCHESTRATE=litellm-orchestrator`, and it is a *different flag* from `--escalate`.** The daemon
  originally passed only `--escalate` (ask the person) and never `--orchestrate` (ask the second
  engine), so every finding was written **without an `orchestration` key** and 指揮者分流 stayed
  empty forever. The loop ran, the findings were written, only that one column never moved.
- **The litellm key is read from `~/Services/litellm/.env`, never stored.** `engines.py` takes
  `QBR_LITELLM_API_KEY` from the environment only. The daemon extracts just
  `LITELLM_MASTER_KEY` from that file (not `source` — it also holds `POSTGRES_PASSWORD`) and exports
  it. Without it the orchestrator correctly refuses: 「需要 API key，但環境變數沒有值；不送未認證的請求」.
- **`QBR_ALLOW_EXTERNAL_LLM=1` is written into the plist on purpose.** The default is off
  (`engines.py::external_egress_allowed`); enabling it is a data-boundary act that has to be visible
  in a file, not inherited from a shell someone once exported in.

**Never `sudo osascript quit` Docker Desktop here** — this host is also the always-on exam platform.
To exercise the watchdog, stop only the container.

### Two rebuild-time gaps, and what was measured about them (2026-09-24)

Neither is a live fault today. Both are things a rebuild from the repo alone would get wrong, which
is exactly the class of defect the plist-in-repo rule above exists to prevent.

**There is no installer for the repair-daemon job.** `deploy/qbr-review/install-watchdog.sh` exists
for the review-UI watchdog (bootout → copy → bootstrap → verify `launchctl list`), and
`deploy_station.sh --restart` calls `up.sh`. Nothing installs `com.qbr.repair-daemon`. So the
sequence above is manual, and a fresh station gets the code but not the job. Measured: the only
`install-*` script under `deploy/qbr-review/` is `install-watchdog.sh`.

**The plist does not set `QBR_LLM_ENV_FILE`.** `repair_daemon.sh` defaults to
`$HOME/Services/litellm/.env`; a station where that file sits elsewhere silently falls through to no
key, and `engines.py` then refuses (correctly) — the orchestrator degrades instead of answering.

What was measured on the station (2026-09-24), so this is a latent risk, not a current failure:

| check | result |
|---|---|
| `~/Services/litellm/.env` exists | ✓ `-rw-------`, 432 bytes |
| `~/qbr-review/queue` writable | ✓ — `scan_state.json` writes work |
| port 4000 listener | Docker (`com.docke`), i.e. **not** in the review-ui container |
| latest findings carry `orchestration` | ✓ `['verdict','why','self_look','model','seconds']` |

That last row is the one that settles it: a degraded orchestrator writes a finding **without** a
usable verdict, so a real `verdict` in the newest records is direct evidence the station's loop is
reaching the model. The `127.0.0.1:4000` in the plist is the **host's** loopback — the loop runs
under launchd on the host, not inside the review-ui container, and `deploy/qbr-review/compose.yaml`
defines no LiteLLM service. A container-side `127.0.0.1` would mean that container, not the host.

### `scan_state.json` must travel with the queue

`deploy_station.sh --queue` copies `review-queues/live/review-ui/` → `queue/review-ui/`, but
`scan_state.json` lives **one level up, at the queue root** — so it was never copied and the
station's 錯題討論區 permanently read 「還沒跑過掃描」 even while a scan was running. The script now
syncs it explicitly and reports 「排隊中 N 題、已看過 M 題」, counted through
`scan_state.pending_keys` rather than a hand-written second reader of that file's schema (the first
attempt used `d.get("done")`, which is not a key that exists, and printed 0).

`scan_state.json` is safe to sync because it is **not** a human artefact — the scan can be re-run.
The findings stream (`question_ai_findings.jsonl`) is in the protected list and is **not** synced by
`deploy_station.sh`, so deploying code does not overwrite what a station has produced.

**Do not over-read that as "the station's stream only ever holds the station's own verdicts."** Two
other paths write the same file: `scripts/push_reviews_to_station.sh` appends laptop findings to the
station's stream on purpose (that is how a laptop's work reaches the reviewer), and the station's own
daemon appends to it every round. So the station file can be a **merged** stream. The accurate claim
is narrow: *`deploy_station.sh` does not sync findings*, which is what keeps a deploy from clobbering
them. `push_reviews_to_station.sh`'s comment that the file is "written by the laptop, the station only
holds what was pushed" did not survive the station loop being deployed, and it should be read as
describing the push direction only.

## `question_review_principles.jsonl` is protected from deletion but is **not** delivered

`deploy_station.sh` lists this stream in `EVENT_STREAMS`, so `--delete` will not remove it. That
protects the station's copy — and it also means **a deploy never brings the laptop's copy over**.

Measured 2026-09-24: the station had **no such file at all**.

```sh
ssh macstudio 'ls -la ~/qbr-review/queue/review-ui/*.jsonl'   # 6 files, no principles stream
ssh macstudio 'find ~/qbr-review -name question_review_principles.jsonl'   # empty
```

The laptop held exactly one principle (the table rule, `p1`, written 2026-09-23) that had never
been applied on the station. So the split was: **the machine that runs the loop and reads the
stream into its prompts had zero principles, and the machine that had one does not run the loop.**
Nothing was broken or lost; the constraint simply never reached a prompt.

**Curate on the station, not on the laptop.** The station is where the UI's 「新增原則」 button
writes (`review_state.append_principle`) and where the daemon reads them, so it is the home:

```sh
ssh macstudio 'cd ~/qbr-review/code/qbr && /usr/bin/python3 scripts/curate_principles_from_comments.py \
  --events ~/qbr-review/queue/review-ui/question_review_events.jsonl \
  --principles ~/qbr-review/queue/review-ui/question_review_principles.jsonl --apply'
```

Verify by reading the number back out of the stream rather than trusting the write:

```sh
ssh macstudio 'cd ~/qbr-review/code/qbr && /usr/bin/python3 -c "
import sys; sys.path.insert(0, \"src\")
from qbr import discuss
print(len(discuss.active_principles(discuss.load_events(
    \"/Users/tim/qbr-review/queue/review-ui/question_review_principles.jsonl\"))))"'
```

`ask_about_blocks.py` also prints 「基本原則 N 條（路徑）」 as its first line, and the count changes
`ai_findings.prompt_version`, so adding or retiring one principle marks every existing finding
**stale** and the next `--restale` re-asks them. Measured: 186 questions.

The same script retires principles that have left its `CURATED` table (append-only: a `remove`
event, not a deletion), so re-running it after editing the table converges instead of leaving two
versions of the same rule in the prompt.

**The inspection direction carries both streams.** `pull_station_reviews.sh` used to pull only
`question_review_events.jsonl`, so the laptop kept whatever principles it had last written itself —
measured 2026-09-24: the station had **7** principle events (5 active) while the laptop had **1**,
and a local dry-run therefore said 「作用中的原則：1 條」 and proposed four rules that were already
in force. It now counts and pulls both streams in one pass:

```sh
scripts/pull_station_reviews.sh --dry-run   # 兩條流的筆數，逐一列出
scripts/pull_station_reviews.sh             # 各自備份後才覆蓋
```

### The station's own round logs were being deleted by every deploy (fixed 2026-09-24)

The code rsync carries `--delete`, and `qbr/runs/` was **not** excluded, so every deploy removed
whatever the station's daemon had written there — the laptop simply has different filenames, so
rsync read them as "extra files in the target". Measured on the station:

```sh
ssh macstudio 'cat ~/qbr-review/logs/repair-daemon.out.log'   # names its own log...
#   [daemon] log=/Users/tim/qbr-review/code/qbr/runs/repair_daemon-20260924-113324.log
ssh macstudio 'ls ~/qbr-review/code/qbr/runs/repair_daemon-20260924-113324.log'   # ...which is gone
```

Of the 31 files in the station's `qbr/runs/`, **every one that carries a 「掃描」 line names the
laptop's queue path** — zero station rounds had survived. The fix is `--exclude='qbr/runs/'`, and the
negative control is a dry run with the old exclude list:

```sh
rsync -an --delete <old excludes> -i ./ macstudio:qbr-review/code/ | grep runs/
#   *deleting qbr/runs/.deploy-sentinel      <- an old-style deploy plans to delete it
```

The consequence was worse than a missing log: reading `runs/` **on the station** returned the
laptop's records, which look identical except that their 「掃描」 line names a path the station does
not even have. Any log left from before this fix is the laptop's; the tell is that path.

**Two repair daemons are installed, one on each machine** (measured 2026-09-24): the station's
(`QUEUE=/Users/tim/qbr-review/queue`, `LANE=dgx-qwen3.8-flash`) and the laptop's
(`~/Library/LaunchAgents/com.qbr.repair-daemon.plist`, plist dated 2026-09-24 01:09, rounds all day
every ~30 min against `qbr/data/review-queues/live`). The section above says the loop and the UI
must be the same machine, and that was done by *installing* the station job — the laptop job was
never retired. Whether it should be is a decision for the owner: the two write findings into
**different** streams, so they do not overwrite each other directly, but they do ask the same
questions of the same engine and one of the streams only reaches the reviewer through
`push_reviews_to_station.sh`.

> **Superseded 2026-09-25 — the laptop job *is* retired, and this paragraph is now the trap it
> warns about.** Measured on the laptop: `launchctl list | grep qbr` prints nothing (no job),
> `~/Library/LaunchAgents/` holds no `com.qbr.repair-daemon.plist`, and the file is at
> `~/Library/LaunchAgents-retired/com.qbr.repair-daemon.plist.retired-20260924`. On the station the
> loaded job is `com.qbr.repair-daemon` from `~/Library/LaunchAgents/com.qbr.repair-daemon.station.plist`
> — read back with `launchctl print gui/$(id -u)/com.qbr.repair-daemon`: `INTERVAL => 600`,
> `StartInterval => 600`, `WINDOW => 60` (owner, 2026-09-25: 「現階段改成10分鐘掃描一次」). A
> **second, unloaded** copy of the retired laptop plist still sits in the station's
> `~/Library/LaunchAgents/`; its own log (`~/qbr-review/logs/launchd-repair-daemon.out.log`) ends
> `2026-09-24T03:38:14Z` with `window=5`. Both labels are `com.qbr.repair-daemon`, so only the
> loaded one matters — but the stale file misled a reader on 2026-09-25 into believing the loop ran
> at 1800s/WINDOW=5. Delete the unloaded copy rather than read it.

**A backfill and the resident loop share one engine, and there is no coordination.** Measured
2026-09-24 while re-asking 186 stale questions with `--concurrency 4` on the station: the daemon's
30-minute round started mid-backfill on the same DGX, and **16 of 108 calls came back
`request failed`** (14.8%). Those failures are now retryable (`ai_findings.is_answer` treats a record
with `finding: None` as unanswered, so `--restale` picks them up again) — before that fix they were
recorded with the current `prompt_version` and would never have been asked again. So the practical
consequence is "the backfill converges over more than one pass", not corruption; expect to run
`--restale` twice rather than assuming one pass finished the list.

## Deploying to another machine (the sequence that worked)

**From the laptop, there are two scripts for this — use them, not hand-run `rsync` + `ssh`:**

```sh
scripts/deploy_station.sh                # sync code to the station + record provenance
scripts/deploy_station.sh --queue        # also sync the queue (after a rebuild)
scripts/deploy_station.sh --restart      # sync, then up.sh on the station
scripts/deploy_station.sh --force        # 明知有迴圈在跑還是要部署（那一輪會少做事）

scripts/pull_station_reviews.sh          # bring the human decisions back (one-way)
```

### Do not deploy while a round is running (the guard, 2026-09-24)

The repair daemon runs rounds on the station every 30 minutes, and a round is a bash script that
calls other scripts **by path**. Replacing those files mid-round does not crash the round: bash reads
a script lazily, so it continues at whatever offset it had reached in the **new** file, and the steps
it never reached are simply skipped. **A round that skips steps looks exactly like a round that
finished.**

Measured, 2026-09-24 18:06:55: a `--restart` deploy replaced `repair_daemon.sh` while a round sat
between ② and ③. That round's log ends right after ② with 「第 1 輪結束」; its ③ (the applier), ④
(the text producer) and ⑤ (the report) never ran, and nothing anywhere said so. The consequence is
not a broken artifact, it is a **missing one** — the very repairs the round was supposed to apply
were silently not applied.

So `deploy_station.sh` now refuses to start when the station has `repair_daemon`, `confirm_dispute`,
`apply_dispute_repairs`, `apply_text_corrections`, `scan_category_principles` or `ask_about_blocks`
running (`pgrep -fl` over the full command line, checked **before any rsync**, exit code 3):

```sh
ssh macstudio 'pgrep -fl "confirm_dispute|repair_daemon"'   # empty means safe to deploy
```

A round in flight is not an emergency: wait for it (`ls -t ~/qbr-review/code/qbr/runs/repair_daemon-*.log
| head -1` names the current log). `--force` exists for the case where the loop is genuinely wedged
and you accept that the running round loses its remaining steps.

`deploy_station.sh` exists because hand-running the copy misses two things silently: the
`code/國考題資料夾` **mountpoint** (see step 3 below) and the **source revision** that
`record-deploy.sh` needs. It never touches the review log — that direction is the pull script's.

Manual sequence, for when something is different enough that the script does not fit:

1. **Recon before touching.** What is on the target port, is the corpus already there (*and is it the
   same bytes?*), is the code there, is Docker's AutoStart on? Copying 20G unnecessarily, or serving
   a stale corpus, are both avoidable by measuring first.
2. **rsync the code** (excluding `國考題資料夾*`, `qbr/data/`, `.git/`, `.venv/`) — ~55M.
3. **`mkdir` the mountpoint** `code/國考題資料夾` in the destination. The compose file mounts the
   corpus *inside* `/workspace`, which is mounted read-only, so Docker cannot create the mountpoint
   and the container fails with `read-only file system`. It must already exist in the source tree.
4. **Copy the corpus subtree the queue actually references** (`by_official_catalog`), then verify
   sha256 against the source for **every** referenced PDF — not just that the paths exist.
5. **Copy the queue**, then verify `candidates.jsonl` sha256 and the crop file count.
6. **Seed the review log** with the existing decisions.
7. **Neutralise any old service on the port** (`docker update --restart=no`) so it cannot come back
   and take the port later.
8. **`up.sh`**, then verify **from another device**: page, `/api/queue_index`, a real image, a real
   PDF, a real **write**, and the navigation contract against the **served** HTML (`curl` it, then
   run `scripts/test_v2_navigation.mjs` on that file — the served bytes, not the repo's).

### What is actually running: `DEPLOYED.json`

The station mirrors the laptop's **working tree**, and a working tree is not a commit. Measured: the
deployed `scripts/serve_question_review_ui.py` carries an **uncommitted** `content_type_of` fix (from
another work-stream) that is what makes images display at the right MIME type. So "what is running"
cannot be answered by `git log` alone.

**Since 2026-09-23 the server is a composition root.** `scripts/serve_question_review_ui.py` is ~347
lines (`parse_args` + `main` + re-exports); the implementation is in `qbr/src/qbr/review_ui/*.py`.
The deploy mirrors the whole tree, so the package goes along — but two consequences matter here:

- **A change to a review-UI module needs `--restart` exactly like a change to the server file.** The
  running container has the old module loaded in memory; `rsync` alone changes the bytes on disk and
  nothing else.
- **`record-deploy.sh` hashes only `scripts/serve_question_review_ui.py` and `review_ui/v2.html`.**
  After the split those two files no longer contain most of the code, so a matching `server_sha256`
  does **not** prove the station is running your modules. Compare the module hashes too, or use
  `--restart` (which rebuilds and re-verifies the served question count) rather than trusting the
  provenance file alone.

`deploy/qbr-review/record-deploy.sh` writes `~/qbr-review/DEPLOYED.json`: source repo + HEAD +
dirty-file count, sha256 of the server, `v2.html`, `candidates.jsonl` and the review log, plus the
live question count. `deploy_station.sh` calls it and passes the **true** source revision over; run
directly on the station it would read the station's **old unrelated checkout** (`3925d978`) and
record the wrong provenance — which it did, once, before the parameter existed.

## Rebuilding the queue while serving

**Pause the service during a rebuild** — rebuilding into the served directory leaves the list and the
candidate file briefly inconsistent (unsolved, `qbr/reports/review_record_safety.md`). Sequence:

```sh
cd tw-national-exam-catalog
docker compose -f deploy/qbr-review/compose.yaml down
# rebuild the queue (build-review-queue skill / up.sh --rebuild), keeping a dated backup
deploy/qbr-review/up.sh
```

`up.sh --rebuild` carries review records automatically (`--carry-from` points at the **queue root**),
and refuses to start if it would carry 0 records while records exist nearby.

<!-- project-map:belongs-to -->
## 這一層在哪（回上層的路）

> **這是本子專屬技能**：只服務這個子專案。其他子專案要用同一件事時，先確認是不是該變成全域共通技能。

- 本層入口：[`../../../AGENTS.md`](../../../AGENTS.md)
- 不確定從哪開始：[`project_map`](../../../../project_map) 是整棵樹的可點擊地圖
- 卡住時的回溯路徑：技能 → 本層 `AGENTS.md` → `project_map` 入口文件鏈 → 傘層 → charter
<!-- /project-map:belongs-to -->
