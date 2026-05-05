# news-briefing

Daily AI news briefing cron job. Runs at 07:00, searches for recent news
across configured topics, clusters articles into stories, cross-references
with a rolling 21-day cache, and delivers a formatted briefing to a Teams
channel via email.

---

## Architecture

```
COORDINATOR.md          orchestrator — runs at cron depth 0
  └── WORKER.md         one per topic — depth 1, Mistral Large
        └── FETCHER.md  one per article URL — depth 2, Mistral Medium
```

**Why three layers:**
- Coordinator owns the cache, merges results, formats and sends the briefing.
- Workers search and cluster independently per topic, enabling parallel
  execution and keeping each worker's context lean.
- Fetchers are isolated to a single URL fetch + summarize + keyword extract,
  preventing article content from flooding the worker's context window.

**Why these models:**
- Coordinator: inherits the main agent model (currently Mistral Large via
  global config). Handles reasoning-heavy tasks: cache diffing, cross-topic
  detection, briefing formatting.
- Worker: `mistral/mistral-large-latest`. Clustering and Jaccard matching
  across 20–50 article payloads requires reliable instruction following.
  Consider downgrading to `mistral/mistral-medium-latest` if clustering
  quality holds up after a few weeks of runs.
- Fetcher: `mistral/mistral-medium-latest`. Single-article summarization
  and keyword extraction — well within Medium's capability. Runs at high
  volume (up to 50 instances per run) so cost matters here.

---

## Files

```
cron-jobs/news-briefing/
├── README.md           this file
├── COORDINATOR.md      coordinator prompt
├── WORKER.md           worker prompt
├── FETCHER.md          fetcher prompt
├── topics.json         topic and search term definitions
├── config.json         delivery configuration (not in version control)
├── cache.json          rolling 21-day story cache (generated, not in vc)
├── cluster.py          Jaccard clustering and cache matching script
└── results/            per-topic JSON results, overwritten each run
```

---

## Configuration

### `config.json`

Create this file manually. Do not commit it — it contains your Teams channel
email address.

```json
{
  "email_to": "your-channel@domain.webhook.office.com",
  "email_from": "openclaw@yourdomain.nl",
  "email_subject": "AI Intelligence Briefing — {date}"
}
```

To find your Teams channel email address: open the channel → `...` menu →
`Get email address`.

### `topics.json`

Defines the topics to search. Each key is a stable topic ID used in the
cache and result files. Add or remove topics here; no prompt changes needed.

```json
{
  "your-topic-id": {
    "topic": "Human-readable topic name",
    "search_terms": [
      "specific search term",
      "another term with OR operator"
    ]
  }
}
```

Keep topic IDs as lowercase slugs with hyphens. Changing an existing ID
will orphan its cached stories.

Story IDs are namespaced by topic: `{topic_id}/{slug}`, e.g.
`"domain-specific-llm/medpalm3-google-clinical"`. This prevents
collisions between topics that independently discover the same story.

---

## OpenClaw config (`openclaw.json`)

```json
{
  "agents": {
    "defaults": {
      "subagents": {
        "maxSpawnDepth": 2,
        "maxChildrenPerAgent": 5,
        "maxConcurrent": 16,
        "runTimeoutSeconds": 900
      }
    }
  },
  "cron": {
    "jobs": [
      {
        "name": "news-briefing",
        "schedule": "0 7 * * *",
        "runTimeoutSeconds": 1800,
        "prompt": "Read cron-jobs/news-briefing/COORDINATOR.md from workspace and execute it."
      }
    ]
  }
}
```

**Note on model overrides:** OpenClaw has a known bug where
`agents.defaults.subagents.model` is not reliably applied. Models are
therefore specified explicitly in the `sessions_spawn` call inside each
prompt (Mistral Large in COORDINATOR.md for workers, Mistral Medium in
WORKER.md for fetchers). If the bug is fixed upstream, this can be
centralized into config instead.

**Worker announce format:** Workers announce `WORKER_DONE:{topic_id}:{N}`
on success and `WORKER_ERROR:{topic_id}:{reason}` on failure. The coordinator
counts these tokens — not free-text strings — so the format must not be
changed.

**Prompt delivery:** `sessions_spawn` does not support a `systemPrompt`
parameter. Instead, the `task` is a plain text message combining the
file-reading instruction and the JSON input data:

```
Read /home/node/.openclaw/workspace/cron-jobs/news-briefing/WORKER.md
from workspace and execute it exactly. Your input data:
{ ... }
```

The child session reads the instruction file from workspace, which governs
all subsequent behaviour. The coordinator uses `context: "isolated"` so
children start with a clean session.

---

## Dependencies

- **Brave Search API** — set `BRAVE_API_KEY` in OpenClaw gateway config.
  Free tier (2000 queries/month) is sufficient for this job.
  Sign up: https://api-dashboard.search.brave.com/register
- **SMTP** — configured in OpenClaw gateway for email delivery.
- **Teams channel email** — see config.json above.

---

## How the cache works

`cache.json` is a flat array of story objects maintained by the coordinator.

- **New stories** are added each run with `first_seen` set to today
  and `referred_to` set to null.
- **Developing stories** (matched to a cached story by Jaccard keyword
  similarity ≥ 0.15) update the cached entry's `last_seen`, set
  `referred_to` to today, and expand its keyword set.
- **Expired stories** are removed only when BOTH conditions are true:
  `first_seen` is more than 21 days ago AND `referred_to` is null or
  more than 7 days ago. A story that keeps generating new coverage
  stays in the cache indefinitely.
- A story may belong to multiple topics (`topics` array). Cross-topic
  stories appear in a dedicated section of the briefing.

Do not hand-edit `cache.json`. If you want to reset it, delete the file —
the coordinator will start fresh.

---

## Briefing format

The email is plain HTML (no CSS, no inline styles) suitable for Teams
channel email rendering. Structure:

```
AI Intelligence Briefing — YYYY-MM-DD

[Topic name]
  New today
    [Story title]
    [Summary]
    [Developing note if applicable]
    [Source links]
  Research          (academic papers)
  From the blogs    (blog posts)

Cross-topic
  [Stories spanning multiple topics]
```

---

## Debugging

Sub-agent logs are available via:

```
/subagents list
/subagents log <id>
```

Result files for the last run are at:
`cron-jobs/news-briefing/results/{topic_id}.json`

If email delivery fails, the briefing HTML is written to:
`cron-jobs/news-briefing/results/briefing-YYYY-MM-DD.html`

---

## Known limitations

- Paywall articles fall back to the search snippet only. Flagged with
  `[paywall]` in the briefing.
- Bot-blocking on some news sites may cause fetch failures. The fetcher
  marks these as `failed: true` and they are silently dropped.
- Jaccard matching is computed by `cluster.py` (exact, not approximate).
  Occasionally a developing story is not recognized as such if terminology
  shifts significantly between days — this is a data quality issue, not a
  computation issue.
- `cluster.py` is called with an absolute path. The default assumes your
  workspace is at `~/.openclaw/workspace`. If your workspace is elsewhere,
  update the path in WORKER.md and COORDINATOR.md.
- `maxChildrenPerAgent: 5` means fetchers run in batches of 5 per worker,
  not all at once. For 50 URLs this means ~10 sequential batches per topic.
