# News briefing topic worker

You are a topic research worker. You search for today's news on a given
topic, spawn fetch agents for each article, cluster results into stories,
cross-reference with the story cache, and write a structured results file.

You do not send output to any channel. You only write a file and announce
its location when done.

---

## Input

Your task arrives as a plain text message. The first line instructs you to
read this file (which you are now reading). The rest contains your input
data as JSON. Extract and parse the JSON block that follows "Your input
data:".

```json
{
  "topic_id": "...",
  "topic": "...",
  "search_terms": ["...", "..."],
  "today": "YYYY-MM-DD",
  "cache": [
    {
      "story_id": "...",
      "keywords": ["..."],
      "summary_snippet": "First sentence of cached summary."
    }
  ]
}
```

`cache` contains only what is needed for matching and developing_note
generation. No full summaries, titles, dates, or other metadata.

---

## Step 1 — Search

For each search term, call `web_search` with:
- `freshness: "day"`
- `count: 10`

Collect all results into a flat list. For each result record:
- `url`
- `title` (from search result)
- `snippet` (the short description — 1 to 3 sentences)
- `publication` (from search result source field)
- `content_type`: `"academic"` if the URL is from arxiv.org,
  semanticscholar.org, dl.acm.org, aclanthology.org, openreview.net,
  biorxiv.org, medrxiv.org, or similar. Otherwise `"news"`.

**Deduplicate** by URL. Keep the first occurrence when a URL appears
across multiple search terms.

**Discard** results from:
- Social media: twitter.com, x.com, reddit.com, linkedin.com, facebook.com,
  youtube.com
- Job listings: greenhouse.io, lever.co, workday.com, careers.*, jobs.*
- Press release aggregators: prnewswire.com, businesswire.com,
  globenewswire.com
- E-commerce or product pages with no editorial content

Cap the list at **30 URLs** after deduplication and discarding. If you have
more than 30, keep the 30 with the most relevant-looking titles.

---

## Step 2 — Fetch articles

Spawn a FETCHER sub-agent for each URL using `sessions_spawn` with:
- `model`: `"mistral/mistral-medium-latest"`
- `context`: `"isolated"`
- `task`: a plain text message combining the file-reading instruction and
  the input data. Format it exactly as:

```
Read /home/node/.openclaw/workspace/cron-jobs/news-briefing/FETCHER.md
from workspace and execute it exactly. Your input data:

{
  "url": "https://...",
  "title": "Search result title",
  "snippet": "Search result snippet",
  "publication": "Search result source",
  "content_type": "news | academic"
}
```

Spawn up to 5 fetchers at a time. Wait up to **120 seconds** for each batch
to announce before spawning the next. If fewer announces arrive than expected
within 120 seconds, treat the missing ones as failed and continue.

**Parsing fetcher announces:** Each fetcher announce arrives as a normalized
message from OpenClaw. Extract the JSON from the `Result:` field of the
announce. The format will be:

```
Status: completed successfully
Result: {"url": "...", "title": "...", ...}
```

Parse only the content after `Result: ` as JSON. Discard any result where
`"failed": true` or `"summary"` is null.

---

## Step 3 — Cluster articles and match against cache

Build the following JSON and write it to
`/tmp/cluster_input_{topic_id}.json`:

```json
{
  "items": [
    {"id": "{url}", "keywords": ["keywords from fetcher result"]}
  ],
  "cache": [
    {"story_id": "{story_id}", "keywords": ["cached keywords"]}
  ],
  "cluster_threshold": 0.2,
  "cache_threshold": 0.15,
  "max_merged_keywords": 20
}
```

Run:

```bash
python3 /home/node/.openclaw/workspace/cron-jobs/news-briefing/cluster.py \
  < /tmp/cluster_input_{topic_id}.json \
  > /tmp/cluster_output_{topic_id}.json
```

Check the exit code. If non-zero, announce
`WORKER_ERROR:{topic_id}:cluster.py failed` and stop.

Read `/tmp/cluster_output_{topic_id}.json`. Each cluster has:
- `item_ids` — URLs in this cluster
- `merged_keywords` — union of keyword sets (capped and filtered)
- `cache_story_ids` — matched cache story IDs (empty if new)

---

## Step 4 — Build story objects from clusters

For each cluster:

- Collect all fetcher result objects whose URL appears in `item_ids`.
- Assign a `story_id`: `"{topic_id}/{slug}"` — slug derived from the most
  distinctive entries in `merged_keywords`. Lowercase, hyphens only, max 6
  words. Example: `"domain-specific-llm/medpalm3-google-clinical"`
- Use `merged_keywords` as the story's keyword set.
- Write a `title`: one concise sentence (max 15 words) identifying the story.
- Write a `summary` of 3–4 sentences synthesizing across all sources in
  English. Do not concatenate. Describe the story once.
- List all sources. For each, carry `detected_language` from the fetcher.
- Set `content_type` by priority:
  1. `"academic_news"` — if both `"academic"` and `"news"` sources present
  2. `"academic"` — only academic sources
  3. `"news"` — news sources (no academic)
  4. `"blog"` — only if all sources are blogs
- Set `topics: ["{topic_id}"]`. Do not speculate about other topics — cross-
  topic detection is handled by the coordinator.
- If `cache_story_ids` is non-empty:
  - Set `"developing": true`
  - Set `"cache_story_ids"` to the matched IDs
  - Write one sentence in `"developing_note"` describing what is new. Use
    the `summary_snippet` from matching cache entries to ground the note:
    e.g. "Previously reported as [summary_snippet]; today's coverage adds
    [what is new]."
- Otherwise: `"developing": false`, `"cache_story_ids": []`

---

## Step 5 — Write results file

Write the following JSON to:
`/home/node/.openclaw/workspace/cron-jobs/news-briefing/results/{topic_id}.json`

Overwrite if the file already exists.

If the write fails, announce `WORKER_ERROR:{topic_id}:write failed` and stop.

```json
{
  "topic_id": "...",
  "topic": "...",
  "date": "YYYY-MM-DD",
  "stories": [
    {
      "story_id": "{topic_id}/{slug}",
      "title": "One concise sentence identifying the story.",
      "first_seen": "YYYY-MM-DD",
      "developing": false,
      "developing_note": null,
      "cache_story_ids": [],
      "content_type": "news",
      "topics": ["topic_id"],
      "summary": "3–4 sentence synthesized summary in English.",
      "keywords": ["merged", "keyword", "set"],
      "sources": [
        {
          "title": "Article title",
          "publication": "Publication name",
          "url": "https://...",
          "detected_language": "en",
          "paywall": false
        }
      ]
    }
  ]
}
```

If no stories were found, write `"stories": []`.

---

## Step 6 — Announce

Announce the following single line and nothing else:

```
WORKER_DONE:{topic_id}:{N}
```

Where `{N}` is the number of stories written (0 or more).
