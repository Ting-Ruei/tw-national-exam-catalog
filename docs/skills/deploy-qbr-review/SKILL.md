---
name: deploy-qbr-review
description: Run the new-pipeline question Review UI as a container on the LAN — start it at boot, open it from another device's browser, verify it is actually serving the right queue, and pull evidence from that machine when something looks wrong. Use when setting up or restarting the review server, when another device cannot reach the review UI, when the interface shows the wrong question count, or when deciding which queue a container is serving.
---

# Deploying the Review UI (new pipeline, LAN)

`tw-national-exam-catalog/deploy/qbr-review/`. It serves the **qbr (new) pipeline's** queue over
HTTP so a reviewer can open it from a browser on another device.

**What this is not:** the old `deploy/ai395/compose.production.yaml` (SQL backend, ports 8765/8766).
Those are a **different review store**. Reviewing the new pipeline and mixing the old one in is the
error this deployment exists to avoid (`qbr/reports/two_review_stores.md`).

## Start it

```sh
cd tw-national-exam-catalog
deploy/qbr-review/up.sh              # build image, prepare queue, start, verify by itself
deploy/qbr-review/up.sh --rebuild    # re-run the queue from packages first (after a code change)
```

Then, from any device on the LAN:

```
http://<this-machine-LAN-IP>:8774/v2
```

At boot: Docker Desktop **AutoStart = True**, container `qbr-review-ui` with
`restart: unless-stopped`. Nothing else is needed — if the machine came up, the server is up.

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

## When something looks wrong — pull evidence from that machine

Do not reason from the other device's screen. On the host:

```sh
cd tw-national-exam-catalog

# Is it up, and what is it serving?
docker compose -f deploy/qbr-review/compose.yaml ps
curl -s http://127.0.0.1:8774/api/queue_index | python3 -m json.tool | head -20

# What does the container itself say?
docker compose -f deploy/qbr-review/compose.yaml logs --tail 80

# Which queue is mounted, and how many records are in it?
docker inspect qbr-review-ui --format '{{range .Mounts}}{{.Source}} -> {{.Destination}} ({{.Mode}})
{{end}}'
wc -l qbr/data/review-queues/live/review-ui/candidates.jsonl

# Is an image actually served (not just the page)?
curl -s -o /dev/null -w '%{http_code} %{content_type}\n' \
     "http://127.0.0.1:8774/queue/review-ui/crops/<paper>/<name>.png"
```

The question to answer first is always: **is it not drawing, or is the thing not there?**
(`build-exam-question-bank/SKILL.md` records the same lesson: the first three explanations were
about rendering; the cause was that the scan never saw the picture.)

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
