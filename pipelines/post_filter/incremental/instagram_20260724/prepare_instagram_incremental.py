#!/usr/bin/env python3
"""Prepare a deduplicated Instagram increment and apply the existing rule filter."""

from __future__ import annotations

import argparse
import csv
import hashlib
import importlib.util
import json
from collections import Counter, OrderedDict
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Iterable, List, Tuple


DEFAULT_NEW_DIR = Path(r"E:\Users\ASUS\OneDrive\Desktop\ig_data")
DEFAULT_OLD_PAIRS = Path(
    r"D:\social_network\benchmark\socialmedia_data"
    r"\relevance_outputs_posts_only_by_platform\Instagram"
    r"\Instagram_post_relevance_pairs.csv"
)
DEFAULT_CLEAN_SCRIPT = Path(
    r"D:\social_network\benchmark\socialmedia_data"
    r"\clean_outputs\clean_multi_platform_data.py"
)
DEFAULT_RULE_SCRIPT = Path(
    r"D:\social_network\benchmark\socialmedia_data"
    r"\relevance_outputs_posts_only\relevance_filter_multi_platform_data.py"
)
DEFAULT_OUTPUT = Path(__file__).resolve().parent


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Normalize new Instagram data, remove post-topic pairs already covered "
            "by the historical rule-filter output, and apply the same rule filter."
        )
    )
    parser.add_argument("--new_input_dir", type=Path, default=DEFAULT_NEW_DIR)
    parser.add_argument("--old_pairs", type=Path, default=DEFAULT_OLD_PAIRS)
    parser.add_argument("--clean_script", type=Path, default=DEFAULT_CLEAN_SCRIPT)
    parser.add_argument("--rule_script", type=Path, default=DEFAULT_RULE_SCRIPT)
    parser.add_argument("--output_dir", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--current_date", default="2026-07-24")
    parser.add_argument("--manual_sample_per_topic", type=int, default=100)
    parser.add_argument("--seed", type=int, default=20260724)
    return parser.parse_args()


def load_module(path: Path, module_name: str):
    spec = importlib.util.spec_from_file_location(module_name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Could not import Python module: {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def write_csv(path: Path, rows: Iterable[Dict[str, Any]], fields: List[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def write_jsonl(path: Path, rows: Iterable[Dict[str, Any]], fields: List[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as stream:
        for row in rows:
            item = {field: row.get(field, "") for field in fields}
            stream.write(json.dumps(item, ensure_ascii=False) + "\n")


def unique(values: Iterable[str]) -> List[str]:
    seen = set()
    result = []
    for value in values:
        value = str(value or "").strip()
        if value and value not in seen:
            seen.add(value)
            result.append(value)
    return result


def split_pipe(value: Any) -> List[str]:
    return [part.strip() for part in str(value or "").split("||") if part.strip()]


def parse_pairs(record: Dict[str, Any]) -> "OrderedDict[str, List[str]]":
    mapping: "OrderedDict[str, List[str]]" = OrderedDict()
    for item in split_pipe(record.get("theme_keyword_pairs")):
        if "::" not in item:
            continue
        topic, keyword = (part.strip() for part in item.split("::", 1))
        if topic:
            mapping.setdefault(topic, [])
            if keyword:
                mapping[topic].append(keyword)
    if mapping:
        for topic in mapping:
            mapping[topic] = unique(mapping[topic])
        return mapping

    topics = split_pipe(record.get("themes")) or split_pipe(record.get("primary_theme"))
    keywords = split_pipe(record.get("keywords")) or split_pipe(record.get("primary_keyword"))
    for topic in unique(topics):
        mapping[topic] = unique(keywords)
    return mapping


def old_pair_keys(path: Path) -> set[Tuple[str, str]]:
    keys: set[Tuple[str, str]] = set()
    with path.open("r", encoding="utf-8-sig", newline="") as stream:
        for row in csv.DictReader(stream):
            mid = str(row.get("mid") or "").strip()
            topic = str(row.get("topic") or "").strip()
            if mid and topic:
                keys.add((mid, topic))
    return keys


def normalize_new_instagram(clean, input_dir: Path, current_date: str):
    files = clean.discover_files(input_dir)
    if not files:
        raise FileNotFoundError(f"No *_data*.txt files found in {input_dir}")

    all_message_records = []
    all_user_records = []
    audit_rows = []
    records_by_file: Dict[str, int] = {}

    for path in files:
        records, errors = clean.read_jsonl(path)
        records_by_file[path.name] = len(records)
        audit_rows.extend(errors)
        for record in records:
            if clean.is_user_record(record, path.name):
                all_user_records.append(record)
            else:
                all_message_records.append(record)

    users_by_key, user_audit = clean.merge_user_records(all_user_records)
    audit_rows.extend(user_audit)
    max_ts = clean.max_timestamp_for_date(current_date)
    clean_rows, message_audit = clean.collapse_messages(
        all_message_records, users_by_key, max_ts
    )
    audit_rows.extend(message_audit)

    posts = [
        row
        for row in clean_rows
        if row.get("platform") == "Instagram" and row.get("record_type") == "post"
    ]
    return posts, audit_rows, records_by_file, files


def build_incremental_records(
    posts: List[Dict[str, Any]], historical_keys: set[Tuple[str, str]]
):
    incremental = []
    historical_exclusions = []
    all_keys: set[Tuple[str, str]] = set()
    overlap_keys: set[Tuple[str, str]] = set()
    duplicate_keys: set[Tuple[str, str]] = set()

    for record in posts:
        mid = str(record.get("mid") or "").strip()
        mapping = parse_pairs(record)
        novel_mapping: "OrderedDict[str, List[str]]" = OrderedDict()
        for topic, keywords in mapping.items():
            key = (mid, topic)
            if key in all_keys:
                duplicate_keys.add(key)
                continue
            all_keys.add(key)
            if key in historical_keys:
                overlap_keys.add(key)
                historical_exclusions.append(
                    {
                        "mid": mid,
                        "native_id": record.get("native_id", ""),
                        "url": record.get("url", ""),
                        "topic": topic,
                        "keyword": " || ".join(keywords),
                        "source_file": record.get("source_file", ""),
                        "reason": "mid_topic_already_in_historical_rule_pairs",
                    }
                )
            else:
                novel_mapping[topic] = keywords

        if not novel_mapping:
            continue

        item = dict(record)
        topics = list(novel_mapping)
        keywords = unique(
            keyword for topic_keywords in novel_mapping.values() for keyword in topic_keywords
        )
        pair_strings = [
            f"{topic} :: {keyword}" if keyword else f"{topic} ::"
            for topic, topic_keywords in novel_mapping.items()
            for keyword in (topic_keywords or [""])
        ]
        item["themes"] = " || ".join(topics)
        item["keywords"] = " || ".join(keywords)
        item["theme_keyword_pairs"] = " || ".join(pair_strings)
        item["primary_theme"] = topics[0]
        item["primary_keyword"] = keywords[0] if keywords else ""
        item["matched_theme_count"] = len(topics)
        item["matched_keyword_count"] = len(keywords)
        incremental.append(item)

    return (
        incremental,
        historical_exclusions,
        all_keys,
        overlap_keys,
        duplicate_keys,
    )


def replace_report_wording(path: Path) -> None:
    text = path.read_text(encoding="utf-8")
    replacements = {
        "X Topic Relevance Filtering Report": (
            "Instagram Incremental Topic Relevance Filtering Report"
        ),
        "Input unique tweets": "Input unique Instagram posts",
    }
    for old, new in replacements.items():
        text = text.replace(old, new)
    path.write_text(text, encoding="utf-8")


def main() -> None:
    args = parse_args()
    for required in [args.new_input_dir, args.old_pairs, args.clean_script, args.rule_script]:
        if not required.exists():
            raise FileNotFoundError(required)

    output_dir = args.output_dir.resolve()
    audit_dir = output_dir / "00_audit"
    dedup_dir = output_dir / "01_deduplicated"
    rule_dir = output_dir / "02_rule_filter"
    qwen_dir = output_dir / "03_qwen_input"
    for directory in [audit_dir, dedup_dir, rule_dir, qwen_dir]:
        directory.mkdir(parents=True, exist_ok=True)

    clean = load_module(args.clean_script.resolve(), "instagram_incremental_clean")
    rule = load_module(args.rule_script.resolve(), "instagram_incremental_rule")

    historical_keys = old_pair_keys(args.old_pairs)
    posts, audit_rows, records_by_file, source_files = normalize_new_instagram(
        clean, args.new_input_dir.resolve(), args.current_date
    )
    (
        incremental,
        historical_exclusions,
        new_keys,
        overlap_keys,
        duplicate_keys,
    ) = build_incremental_records(posts, historical_keys)

    historical_exclusion_fields = [
        "mid",
        "native_id",
        "url",
        "topic",
        "keyword",
        "source_file",
        "reason",
    ]
    write_csv(
        audit_dir / "historical_mid_topic_pairs_excluded.csv",
        historical_exclusions,
        historical_exclusion_fields,
    )
    raw_post_normalization_exclusions = [
        row
        for row in audit_rows
        if row.get("platform") == "Instagram"
        and row.get("record_type") == "post"
    ]
    write_csv(
        audit_dir / "new_batch_posts_excluded_during_normalization.csv",
        raw_post_normalization_exclusions,
        clean.AUDIT_FIELDS,
    )

    dedup_jsonl = dedup_dir / "instagram_incremental_posts.jsonl"
    dedup_csv = dedup_dir / "instagram_incremental_posts.csv"
    write_jsonl(dedup_jsonl, incremental, clean.MESSAGE_FIELDS)
    write_csv(dedup_csv, incremental, clean.MESSAGE_FIELDS)

    pair_rows = rule.build_pair_rows(incremental, mode="rule")
    pair_rows.sort(
        key=lambda row: (
            str(row.get("topic") or ""),
            str(row.get("mid") or ""),
        )
    )
    kept_rows = [
        row for row in pair_rows if row["relevance_label"] in rule.KEEP_LABELS
    ]
    removed_rows = [
        row for row in pair_rows if row["relevance_label"] in rule.REMOVE_LABELS
    ]
    topic_summary = rule.summarize_topics(pair_rows)
    keyword_summary = rule.summarize_keywords(pair_rows)
    manual_sample = rule.make_manual_review_sample(
        pair_rows, args.manual_sample_per_topic, args.seed
    )

    rule.write_jsonl(
        rule_dir / "instagram_incremental_post_relevance_pairs.jsonl",
        pair_rows,
        rule.PAIR_FIELDS,
    )
    rule.write_csv(
        rule_dir / "instagram_incremental_post_relevance_pairs.csv",
        pair_rows,
        rule.PAIR_FIELDS,
    )
    rule.write_jsonl(
        rule_dir / "instagram_incremental_post_relevance_filtered.jsonl",
        kept_rows,
        rule.PAIR_FIELDS,
    )
    rule.write_csv(
        rule_dir / "instagram_incremental_post_relevance_filtered.csv",
        kept_rows,
        rule.PAIR_FIELDS,
    )
    rule.write_csv(
        rule_dir / "instagram_incremental_post_relevance_removed.csv",
        removed_rows,
        rule.PAIR_FIELDS,
    )
    rule.write_csv(
        rule_dir / "instagram_incremental_post_topic_summary.csv",
        topic_summary,
        rule.TOPIC_SUMMARY_FIELDS,
    )
    rule.write_csv(
        rule_dir / "instagram_incremental_post_keyword_summary.csv",
        keyword_summary,
        rule.KEYWORD_SUMMARY_FIELDS,
    )
    rule.write_csv(
        rule_dir / "instagram_incremental_post_manual_review_sample.csv",
        manual_sample,
        rule.MANUAL_SAMPLE_FIELDS,
    )
    report_path = rule_dir / "instagram_incremental_post_relevance_report.html"
    rule.generate_html_report(
        report_path,
        input_tweets=len(incremental),
        pair_rows=pair_rows,
        topic_summary=topic_summary,
        keyword_summary=keyword_summary,
        effective_mode="rule",
    )
    replace_report_wording(report_path)

    qwen_input = qwen_dir / "instagram_incremental_qwen_input.csv"
    rule.write_csv(qwen_input, kept_rows, rule.PAIR_FIELDS)

    label_counts = Counter(row["relevance_label"] for row in pair_rows)
    source_hashes = {str(path): sha256(path) for path in source_files}
    source_hashes[str(args.old_pairs.resolve())] = sha256(args.old_pairs.resolve())
    manifest = {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "scope": "New Instagram posts only; comments are not part of this run.",
        "deduplication_key": ["mid", "topic"],
        "deduplication_reference": str(args.old_pairs.resolve()),
        "cleaning_script": str(args.clean_script.resolve()),
        "rule_filter_script": str(args.rule_script.resolve()),
        "cleaning_filter_versions": {
            "rule_filter_version": rule.FILTER_VERSION,
            "current_date_used_for_time_quality_flag_only": args.current_date,
        },
        "counts": {
            "raw_records_by_file": records_by_file,
            "normalized_instagram_posts": len(posts),
            "new_unique_post_topic_pairs": len(new_keys),
            "already_in_historical_pairs": len(overlap_keys),
            "duplicate_pairs_inside_new_batch": len(duplicate_keys),
            "raw_posts_excluded_during_normalization": len(
                raw_post_normalization_exclusions
            ),
            "raw_posts_excluded_as_empty_text": sum(
                1
                for row in raw_post_normalization_exclusions
                if row.get("reason") == "empty_text"
            ),
            "incremental_post_records": len(incremental),
            "incremental_post_topic_pairs": len(pair_rows),
            "rule_kept_for_qwen": len(kept_rows),
            "rule_removed_before_qwen": len(removed_rows),
            "topics_in_increment": len(topic_summary),
            "normalization_audit_rows": len(audit_rows),
        },
        "rule_label_counts": dict(label_counts),
        "source_sha256": source_hashes,
        "outputs": {
            "historical_pairs_excluded_csv": str(
                audit_dir / "historical_mid_topic_pairs_excluded.csv"
            ),
            "new_batch_normalization_exclusions_csv": str(
                audit_dir / "new_batch_posts_excluded_during_normalization.csv"
            ),
            "deduplicated_csv": str(dedup_csv),
            "deduplicated_jsonl": str(dedup_jsonl),
            "rule_pairs_csv": str(
                rule_dir / "instagram_incremental_post_relevance_pairs.csv"
            ),
            "rule_filtered_csv": str(
                rule_dir / "instagram_incremental_post_relevance_filtered.csv"
            ),
            "rule_removed_csv": str(
                rule_dir / "instagram_incremental_post_relevance_removed.csv"
            ),
            "qwen_input_csv": str(qwen_input),
            "rule_report_html": str(report_path),
        },
    }
    manifest_path = output_dir / "manifest.json"
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    print(f"Normalized Instagram posts: {len(posts)}")
    print(f"New post-topic pairs: {len(new_keys)}")
    print(f"Already processed pairs removed: {len(overlap_keys)}")
    print(f"Incremental pairs sent to rule filter: {len(pair_rows)}")
    print(f"Rule-kept pairs prepared for Qwen: {len(kept_rows)}")
    print(f"Rule-removed pairs: {len(removed_rows)}")
    print(f"Manifest: {manifest_path}")


if __name__ == "__main__":
    main()
