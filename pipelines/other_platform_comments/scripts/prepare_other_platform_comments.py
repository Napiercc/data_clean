#!/usr/bin/env python3
"""Prepare non-Facebook comments for later topic-relevance annotation.

The script treats the old socialmedia_data corpus as read-only. It joins cleaned
Instagram and YouTube comments to the current retained post/video set, expands
each comment across the retained post's distinct topics, and writes a compact,
auditable CSV/JSONL pair under data_clean.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlsplit


PACKAGE_ROOT = Path(__file__).resolve().parents[1]
PIPELINES_ROOT = PACKAGE_ROOT.parent
DATA_CLEAN_ROOT = PIPELINES_ROOT.parent
BENCHMARK_ROOT = DATA_CLEAN_ROOT.parent
DEFAULT_POSTS = (
    PIPELINES_ROOT
    / "post_filter"
    / "output"
    / "qwen32b_8gpu"
    / "merged"
    / "llm_post_relevance_filtered.csv"
)
DEFAULT_MESSAGES = (
    BENCHMARK_ROOT / "socialmedia_data" / "clean_outputs" / "messages_clean.jsonl"
)
DEFAULT_RAW_DIR = BENCHMARK_ROOT / "socialmedia_data"
DEFAULT_OUTPUT_DIR = PACKAGE_ROOT / "output"
TARGET_PLATFORMS = ("Instagram", "Reddit", "X", "YouTube")

OUTPUT_FIELDS = [
    "annotation_row_id",
    "platform",
    "topic",
    "post_mid",
    "post_native_id",
    "post_url",
    "post_text",
    "post_keywords",
    "post_llm_row_keys",
    "comment_mid",
    "comment_native_id",
    "comment_url",
    "commenter_id",
    "commenter_username",
    "commenter_display_name",
    "comment_text",
    "comment_language",
    "comment_created_at",
    "comment_like_count",
    "reply_count",
    "parent_comment_id",
    "source_parent_post_id",
    "source_parent_post_url",
    "match_method",
    "source_file",
]

SUMMARY_FIELDS = [
    "platform",
    "retained_post_topic_pairs",
    "retained_unique_posts",
    "raw_comment_rows",
    "raw_nonempty_comment_rows",
    "raw_empty_comment_rows",
    "clean_comment_rows",
    "matched_unique_posts",
    "matched_unique_comments",
    "annotation_rows",
    "unmatched_clean_comments",
    "post_coverage_percent",
    "clean_comment_match_percent",
    "comments_with_parent_comment_id",
    "relation_methods",
]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Prepare Instagram and YouTube comments for later annotation."
    )
    parser.add_argument("--retained-posts", type=Path, default=DEFAULT_POSTS)
    parser.add_argument("--messages-jsonl", type=Path, default=DEFAULT_MESSAGES)
    parser.add_argument("--raw-comment-dir", type=Path, default=DEFAULT_RAW_DIR)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    return parser.parse_args()


def text(value: Any) -> str:
    return "" if value is None else str(value).strip()


def as_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def file_state(path: Path) -> dict[str, Any]:
    stat = path.stat()
    return {"path": str(path), "size": stat.st_size, "mtime_ns": stat.st_mtime_ns}


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def normalise_relation_url(value: Any) -> str:
    raw = text(value)
    if not raw:
        return ""
    parsed = urlsplit(raw)
    host = parsed.netloc.lower().removeprefix("www.")
    path = parsed.path.rstrip("/")
    if not host or not path:
        return ""
    if host in {"youtube.com", "m.youtube.com"} and path == "/watch":
        video_id = parse_qs(parsed.query).get("v", [""])[0]
        if video_id:
            return f"youtube.com/watch?v={video_id}"
    return f"{host}{path}"


def add_unique_lookup(
    lookup: dict[tuple[str, str], str],
    ambiguous: set[tuple[str, str]],
    platform: str,
    value: str,
    post_mid: str,
) -> None:
    if not value:
        return
    key = (platform, value)
    if key in ambiguous:
        return
    previous = lookup.get(key)
    if previous is None:
        lookup[key] = post_mid
    elif previous != post_mid:
        ambiguous.add(key)
        lookup.pop(key, None)


def read_retained_posts(path: Path) -> tuple[
    dict[str, dict[str, Any]],
    Counter[str],
    dict[tuple[str, str], str],
    dict[tuple[str, str], str],
    set[tuple[str, str]],
    set[tuple[str, str]],
]:
    roots: dict[str, dict[str, Any]] = {}
    pair_counts: Counter[str] = Counter()
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        for row in csv.DictReader(handle):
            platform = text(row.get("platform"))
            if platform not in TARGET_PLATFORMS:
                continue
            pair_counts[platform] += 1
            post_mid = text(row.get("mid") or row.get("record_id"))
            if not post_mid:
                continue
            root = roots.setdefault(
                post_mid,
                {
                    "post_mid": post_mid,
                    "platform": platform,
                    "post_native_id": text(row.get("native_id")),
                    "post_url": text(row.get("url")),
                    "post_text": text(row.get("cont_clean")),
                    "topics": defaultdict(lambda: {"keywords": set(), "llm_row_keys": set()}),
                },
            )
            topic = text(row.get("topic"))
            if topic:
                root["topics"][topic]["keywords"].add(text(row.get("keyword")))
                root["topics"][topic]["llm_row_keys"].add(text(row.get("llm_row_key")))

    native_lookup: dict[tuple[str, str], str] = {}
    url_lookup: dict[tuple[str, str], str] = {}
    ambiguous_native: set[tuple[str, str]] = set()
    ambiguous_url: set[tuple[str, str]] = set()
    for post_mid, root in roots.items():
        add_unique_lookup(
            native_lookup,
            ambiguous_native,
            root["platform"],
            root["post_native_id"],
            post_mid,
        )
        add_unique_lookup(
            url_lookup,
            ambiguous_url,
            root["platform"],
            normalise_relation_url(root["post_url"]),
            post_mid,
        )
    return (
        roots,
        pair_counts,
        native_lookup,
        url_lookup,
        ambiguous_native,
        ambiguous_url,
    )


def annotation_row_id(platform: str, post_mid: str, topic: str, comment_mid: str) -> str:
    payload = "\n".join((platform, post_mid, topic, comment_mid))
    return hashlib.sha1(payload.encode("utf-8")).hexdigest()


def infer_raw_platform(path: Path) -> str:
    name = path.name.lower()
    if name.startswith("ig_"):
        return "Instagram"
    if name.startswith("ytb_"):
        return "YouTube"
    if name.startswith("red_"):
        return "Reddit"
    if name.startswith("x_"):
        return "X"
    if name.startswith("fb_"):
        return "Facebook"
    return "unknown"


def audit_raw_comment_sources(raw_dir: Path) -> tuple[dict[str, Counter[str]], list[Path]]:
    stats: dict[str, Counter[str]] = defaultdict(Counter)
    paths = sorted(path for path in raw_dir.glob("*comment_data*.txt") if path.is_file())
    for path in paths:
        platform = infer_raw_platform(path)
        if platform not in TARGET_PLATFORMS:
            continue
        with path.open("r", encoding="utf-8-sig", errors="replace") as handle:
            for line in handle:
                if not line.strip():
                    continue
                stats[platform]["raw_comment_rows"] += 1
                try:
                    record = json.loads(line)
                except json.JSONDecodeError:
                    stats[platform]["raw_parse_errors"] += 1
                    continue
                if text(record.get("cont")):
                    stats[platform]["raw_nonempty_comment_rows"] += 1
                else:
                    stats[platform]["raw_empty_comment_rows"] += 1
    return stats, paths


def write_outputs(args: argparse.Namespace) -> dict[str, Any]:
    for required in (args.retained_posts, args.messages_jsonl):
        if not required.exists():
            raise SystemExit(f"Required input not found: {required}")

    raw_stats, raw_paths = audit_raw_comment_sources(args.raw_comment_dir)
    watched_paths = [args.retained_posts, args.messages_jsonl, *raw_paths]
    source_states_before = {str(path): file_state(path) for path in watched_paths}
    retained_sha256 = sha256_file(args.retained_posts)

    (
        roots,
        pair_counts,
        native_lookup,
        url_lookup,
        ambiguous_native,
        ambiguous_url,
    ) = read_retained_posts(args.retained_posts)

    args.output_dir.mkdir(parents=True, exist_ok=True)
    csv_path = args.output_dir / "matched_comments_for_annotation.csv"
    jsonl_path = args.output_dir / "matched_comments_for_annotation.jsonl"
    summary_csv_path = args.output_dir / "comment_preparation_summary.csv"
    summary_json_path = args.output_dir / "comment_preparation_summary.json"
    csv_tmp = csv_path.with_suffix(csv_path.suffix + ".tmp")
    jsonl_tmp = jsonl_path.with_suffix(jsonl_path.suffix + ".tmp")

    clean_comments: Counter[str] = Counter()
    unmatched_comments: Counter[str] = Counter()
    matched_comments: Counter[str] = Counter()
    annotation_rows: Counter[str] = Counter()
    comments_with_parent: Counter[str] = Counter()
    matched_posts: dict[str, set[str]] = defaultdict(set)
    match_methods: Counter[tuple[str, str]] = Counter()
    duplicate_matches: Counter[str] = Counter()
    seen_comments: set[tuple[str, str]] = set()

    with (
        csv_tmp.open("w", encoding="utf-8-sig", newline="") as csv_handle,
        jsonl_tmp.open("w", encoding="utf-8", newline="\n") as jsonl_handle,
        args.messages_jsonl.open("r", encoding="utf-8-sig") as source_handle,
    ):
        writer = csv.DictWriter(csv_handle, fieldnames=OUTPUT_FIELDS)
        writer.writeheader()
        for line in source_handle:
            if not line.strip():
                continue
            record = json.loads(line)
            if text(record.get("record_type")) != "comment":
                continue
            platform = text(record.get("platform"))
            if platform not in TARGET_PLATFORMS:
                continue
            clean_comments[platform] += 1
            r_mid = text(record.get("r_mid"))
            f_mid = text(record.get("f_mid"))
            r_url = normalise_relation_url(record.get("r_url"))
            f_url = normalise_relation_url(record.get("f_url"))
            candidates = (
                ("r_native", native_lookup.get((platform, r_mid)) if r_mid else None),
                ("r_url", url_lookup.get((platform, r_url)) if r_url else None),
                ("f_native", native_lookup.get((platform, f_mid)) if f_mid else None),
                ("f_url", url_lookup.get((platform, f_url)) if f_url else None),
            )
            post_mid = ""
            match_method = ""
            for candidate_method, candidate_post_mid in candidates:
                if candidate_post_mid:
                    post_mid = candidate_post_mid
                    match_method = candidate_method
                    break
            if not post_mid:
                unmatched_comments[platform] += 1
                continue

            comment_mid = text(record.get("mid") or record.get("record_id"))
            comment_text = text(record.get("cont_clean") or record.get("cont"))
            if not comment_mid or not comment_text:
                unmatched_comments[platform] += 1
                continue
            seen_key = (post_mid, comment_mid)
            if seen_key in seen_comments:
                duplicate_matches[platform] += 1
                continue
            seen_comments.add(seen_key)

            root = roots[post_mid]
            matched_comments[platform] += 1
            matched_posts[platform].add(post_mid)
            match_methods[(platform, match_method)] += 1
            parent_comment_id = f_mid
            if parent_comment_id:
                comments_with_parent[platform] += 1

            for topic in sorted(root["topics"]):
                provenance = root["topics"][topic]
                row = {
                    "annotation_row_id": annotation_row_id(
                        platform, post_mid, topic, comment_mid
                    ),
                    "platform": platform,
                    "topic": topic,
                    "post_mid": post_mid,
                    "post_native_id": root["post_native_id"],
                    "post_url": root["post_url"],
                    "post_text": root["post_text"],
                    "post_keywords": " || ".join(
                        sorted(value for value in provenance["keywords"] if value)
                    ),
                    "post_llm_row_keys": " || ".join(
                        sorted(value for value in provenance["llm_row_keys"] if value)
                    ),
                    "comment_mid": comment_mid,
                    "comment_native_id": text(record.get("native_id")),
                    "comment_url": text(record.get("url")),
                    "commenter_id": text(record.get("uid")),
                    "commenter_username": text(record.get("aname")),
                    "commenter_display_name": text(record.get("sname")),
                    "comment_text": comment_text,
                    "comment_language": text(record.get("lang")),
                    "comment_created_at": text(record.get("pt_iso_utc")),
                    "comment_like_count": as_int(record.get("nlike")),
                    "reply_count": as_int(record.get("nrply")),
                    "parent_comment_id": parent_comment_id,
                    "source_parent_post_id": r_mid,
                    "source_parent_post_url": text(record.get("r_url")),
                    "match_method": match_method,
                    "source_file": text(record.get("source_file")),
                }
                writer.writerow(row)
                jsonl_handle.write(json.dumps(row, ensure_ascii=False) + "\n")
                annotation_rows[platform] += 1

    csv_tmp.replace(csv_path)
    jsonl_tmp.replace(jsonl_path)

    summary_rows: list[dict[str, Any]] = []
    for platform in TARGET_PLATFORMS:
        platform_roots = [
            post_mid for post_mid, root in roots.items() if root["platform"] == platform
        ]
        methods = " / ".join(
            f"{method}:{count:,}"
            for (method_platform, method), count in sorted(match_methods.items())
            if method_platform == platform
        )
        raw = raw_stats[platform]
        summary_rows.append(
            {
                "platform": platform,
                "retained_post_topic_pairs": pair_counts[platform],
                "retained_unique_posts": len(platform_roots),
                "raw_comment_rows": raw["raw_comment_rows"],
                "raw_nonempty_comment_rows": raw["raw_nonempty_comment_rows"],
                "raw_empty_comment_rows": raw["raw_empty_comment_rows"],
                "clean_comment_rows": clean_comments[platform],
                "matched_unique_posts": len(matched_posts[platform]),
                "matched_unique_comments": matched_comments[platform],
                "annotation_rows": annotation_rows[platform],
                "unmatched_clean_comments": unmatched_comments[platform],
                "post_coverage_percent": round(
                    100 * len(matched_posts[platform]) / len(platform_roots), 4
                )
                if platform_roots
                else 0.0,
                "clean_comment_match_percent": round(
                    100 * matched_comments[platform] / clean_comments[platform], 4
                )
                if clean_comments[platform]
                else 0.0,
                "comments_with_parent_comment_id": comments_with_parent[platform],
                "relation_methods": methods,
            }
        )

    with summary_csv_path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=SUMMARY_FIELDS)
        writer.writeheader()
        writer.writerows(summary_rows)

    source_states_after = {str(path): file_state(path) for path in watched_paths}
    if source_states_before != source_states_after:
        raise SystemExit("A source file changed while comments were being prepared")

    summary = {
        "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "retained_posts_path": str(args.retained_posts),
        "retained_posts_sha256": retained_sha256,
        "messages_jsonl_path": str(args.messages_jsonl),
        "raw_comment_dir": str(args.raw_comment_dir),
        "output_csv": str(csv_path),
        "output_jsonl": str(jsonl_path),
        "output_field_count": len(OUTPUT_FIELDS),
        "annotation_unit": "one comment x one retained post x one topic",
        "source_files_read_only": True,
        "ambiguous_native_keys": len(ambiguous_native),
        "ambiguous_url_keys": len(ambiguous_url),
        "duplicate_matched_comments_skipped": dict(duplicate_matches),
        "totals": {
            "retained_post_topic_pairs": sum(pair_counts.values()),
            "retained_unique_posts": len(roots),
            "clean_comment_rows": sum(clean_comments.values()),
            "matched_unique_posts": sum(len(values) for values in matched_posts.values()),
            "matched_unique_comments": sum(matched_comments.values()),
            "annotation_rows": sum(annotation_rows.values()),
            "comments_with_parent_comment_id": sum(comments_with_parent.values()),
        },
        "platforms": summary_rows,
        "notes": [
            "Only non-Facebook platforms are included.",
            "Instagram currently joins by normalized r_url; YouTube joins by r_mid/native video ID.",
            "Reddit raw comment rows have empty text and therefore produce no annotation rows.",
            "X has no source comment file in the supplied socialmedia_data snapshot.",
            "parent_comment_id is preserved when present; the current matched data has no usable parent-comment links.",
        ],
        "source_states": list(source_states_after.values()),
    }
    summary_json_path.write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return summary


def main() -> None:
    args = parse_args()
    summary = write_outputs(args)
    totals = summary["totals"]
    print(f"Wrote {summary['output_csv']}")
    print(f"Wrote {summary['output_jsonl']}")
    print(
        "Matched "
        f"{totals['matched_unique_comments']:,} unique comments to "
        f"{totals['matched_unique_posts']:,} retained posts/videos; "
        f"annotation rows={totals['annotation_rows']:,}"
    )
    print(
        "Parent comment IDs available: "
        f"{totals['comments_with_parent_comment_id']:,}"
    )


if __name__ == "__main__":
    main()
