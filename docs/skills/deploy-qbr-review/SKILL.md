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

## Deploying to another machine (the sequence that worked)

**From the laptop, there are two scripts for this — use them, not hand-run `rsync` + `ssh`:**

```sh
scripts/deploy_station.sh                # sync code to the station + record provenance
scripts/deploy_station.sh --queue        # also sync the queue (after a rebuild)
scripts/deploy_station.sh --restart      # sync, then up.sh on the station

scripts/pull_station_reviews.sh          # bring the human decisions back (one-way)
```

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
