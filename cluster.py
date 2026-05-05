#!/usr/bin/env python3
"""
Jaccard-based clustering and cache matching for news-briefing.

Reads JSON from stdin, writes JSON to stdout.

Input schema:
{
  "items": [
    {"id": "...", "keywords": ["..."]}
  ],
  "cache": [
    {"story_id": "...", "keywords": ["..."]}
  ],
  "cluster_threshold": 0.2,
  "cache_threshold": 0.15,
  "max_merged_keywords": 20
}

Output schema:
{
  "clusters": [
    {
      "item_ids": ["...", "..."],
      "merged_keywords": ["..."],
      "cache_story_ids": ["..."]
    }
  ]
}

Frequency filter: keywords appearing in more than 30% of cache entries are
treated as too generic and excluded from Jaccard computation. This prevents
common terms from creating false matches across unrelated stories.

Merged keyword sets are capped at max_merged_keywords (default 20) to
prevent unbounded growth as stories develop over multiple days.
"""

import json
import sys
from itertools import combinations


def jaccard(a, b):
    sa, sb = set(a), set(b)
    union = sa | sb
    if not union:
        return 0.0
    return len(sa & sb) / len(union)


def build_frequency_filter(cache, threshold=0.3):
    """Return set of keywords appearing in > threshold fraction of cache entries."""
    if not cache:
        return set()
    counts = {}
    for entry in cache:
        for kw in set(entry.get("keywords", [])):
            counts[kw] = counts.get(kw, 0) + 1
    cutoff = len(cache) * threshold
    return {kw for kw, count in counts.items() if count > cutoff}


def filter_keywords(keywords, generic):
    return [kw for kw in keywords if kw not in generic]


def cluster(items, threshold):
    """Union-find clustering with transitive closure."""
    n = len(items)
    parent = list(range(n))

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(x, y):
        px, py = find(x), find(y)
        if px != py:
            parent[px] = py

    for i, j in combinations(range(n), 2):
        if jaccard(items[i]["keywords"], items[j]["keywords"]) >= threshold:
            union(i, j)

    groups = {}
    for i in range(n):
        root = find(i)
        groups.setdefault(root, []).append(i)

    return list(groups.values())


def main():
    data = json.load(sys.stdin)
    items = data.get("items", [])
    cache = data.get("cache", [])
    cluster_threshold = data.get("cluster_threshold", 0.2)
    cache_threshold = data.get("cache_threshold", 0.15)
    max_merged = data.get("max_merged_keywords", 20)

    if not items:
        print(json.dumps({"clusters": []}))
        return

    # Build frequency filter from cache
    generic = build_frequency_filter(cache, threshold=0.3)

    # Filter generic keywords from items before clustering
    filtered_items = [
        {"id": item["id"], "keywords": filter_keywords(item.get("keywords", []), generic)}
        for item in items
    ]

    # Filter generic keywords from cache before matching
    filtered_cache = [
        {"story_id": e["story_id"], "keywords": filter_keywords(e.get("keywords", []), generic)}
        for e in cache
    ]

    groups = cluster(filtered_items, cluster_threshold)
    item_map = {item["id"]: item["keywords"] for item in filtered_items}

    result_clusters = []
    for group in groups:
        item_ids = [filtered_items[i]["id"] for i in group]

        merged = set()
        for i in group:
            merged.update(filtered_items[i]["keywords"])

        # Cap merged keyword set
        if len(merged) > max_merged:
            # Keep the most specific (longest) terms when capping
            merged = set(sorted(merged, key=len, reverse=True)[:max_merged])

        merged_list = list(merged)

        matched_cache = [
            entry["story_id"]
            for entry in filtered_cache
            if jaccard(merged_list, entry["keywords"]) >= cache_threshold
        ]

        result_clusters.append({
            "item_ids": item_ids,
            "merged_keywords": merged_list,
            "cache_story_ids": matched_cache,
        })

    print(json.dumps({"clusters": result_clusters}, indent=2))


if __name__ == "__main__":
    main()
