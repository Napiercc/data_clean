#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Second-stage LLM filtering for posts-only cross-platform relevance results.

Input:
  post_relevance_filtered.csv from the rule-based posts-only pipeline.

Output:
  LLM-refined pairs, filtered rows, removed rows, review rows, and summaries.

The LLM judges three separate dimensions:
  1) topic relevance
  2) whether the post has a stance
  3) whether the post has discussion potential

Only rows that pass all three criteria are kept:
  topic_relevance in {strongly_relevant, relevant}
  has_stance is true
  discussion_potential in {high, medium}
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import random
import sys
import threading
import time
import urllib.error
import urllib.request
from collections import Counter, defaultdict
from concurrent.futures import FIRST_COMPLETED, Future, ThreadPoolExecutor, wait
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple


DEFAULT_INPUT = Path("input/post_relevance_filtered.csv")
DEFAULT_OUTPUT_DIR = Path("output/qwen32b_full")

DEFAULT_MODEL = "../models/Qwen3-32B"
KEEP_TOPIC_RELEVANCE = {"strongly_relevant", "relevant"}
KEEP_DISCUSSION = {"high", "medium"}
DEFAULT_MAX_POST_CHARS = 2500
DEFAULT_MAX_OUTPUT_TOKENS = 1024

SYSTEM_PROMPT = """You are a strict second-stage reviewer for a social media research dataset.

Goal:
Keep only posts that are genuinely close to the assigned topic, express or imply a stance, and are likely to invite public discussion.

Rules:
- Judge the post against the assigned topic, not against the keyword alone.
- Do not keep a post only because it mentions a keyword.
- The post must discuss the topic, the policy issue, public debate, directly related event, or a directly related personal experience.
- A factual headline or neutral news summary may be topic-relevant, but it has no stance unless it endorses, criticizes, evaluates, worries about, demands, mocks, or otherwise takes a position.
- Stance means a position, evaluation, preference, concern, endorsement, opposition, criticism, demand, or implied attitude toward the topic/policy/debate.
- Do not treat general emotion as stance unless it is directed at the assigned topic.
- Discussion potential means the text itself is likely to invite disagreement, replies, debate, agreement/disagreement, or public reaction.
- Do not use likes, replies, views, reposts, or author popularity to judge discussion potential.
- If the text looks like an ad, spam, generic entertainment, job post, or unrelated personal update, do not keep it.
- If the text is truncated or too vague to judge, mark insufficient_context or unclear as appropriate.
- Keep final_keep true only if topic_relevance is strongly_relevant or relevant, has_stance is true, and discussion_potential is high or medium.
- Keep every reason field to at most 16 words, with no quotation marks or line breaks.
"""

OUTPUT_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "topic_relevance": {
            "type": "string",
            "enum": [
                "strongly_relevant",
                "relevant",
                "weak_keyword_match",
                "off_topic",
                "insufficient_context",
            ],
        },
        "relevance_reason": {"type": "string"},
        "has_stance": {"type": "boolean"},
        "stance_label": {
            "type": "string",
            "enum": [
                "support",
                "oppose",
                "mixed_or_complex",
                "neutral_or_factual",
                "unclear",
            ],
        },
        "stance_target": {"type": "string"},
        "stance_reason": {"type": "string"},
        "discussion_potential": {
            "type": "string",
            "enum": ["high", "medium", "low", "unclear"],
        },
        "discussion_reason": {"type": "string"},
        "final_keep": {"type": "boolean"},
        "final_keep_reason": {"type": "string"},
        "confidence": {"type": "number", "minimum": 0.0, "maximum": 1.0},
        "needs_human_review": {"type": "boolean"},
    },
    "required": [
        "topic_relevance",
        "relevance_reason",
        "has_stance",
        "stance_label",
        "stance_target",
        "stance_reason",
        "discussion_potential",
        "discussion_reason",
        "final_keep",
        "final_keep_reason",
        "confidence",
        "needs_human_review",
    ],
}

LLM_FIELDS = [
    "llm_topic_relevance",
    "llm_relevance_reason",
    "llm_has_stance",
    "llm_stance_label",
    "llm_stance_target",
    "llm_stance_reason",
    "llm_discussion_potential",
    "llm_discussion_reason",
    "llm_final_keep_model",
    "llm_final_keep",
    "llm_final_keep_reason",
    "llm_confidence",
    "llm_needs_human_review",
    "llm_model",
    "llm_response_id",
    "llm_input_tokens",
    "llm_output_tokens",
    "llm_total_tokens",
    "llm_error",
    "llm_processed_at",
    "llm_row_key",
]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run LLM second-stage filtering on posts-only relevance rows.")
    parser.add_argument("--input", default=str(DEFAULT_INPUT), help="Input post_relevance_filtered.csv path.")
    parser.add_argument("--output_dir", default=str(DEFAULT_OUTPUT_DIR), help="Directory for LLM refined outputs.")
    parser.add_argument("--model", default=DEFAULT_MODEL, help="Model name served by vLLM. Default: ../models/Qwen3-32B.")
    parser.add_argument(
        "--base_url",
        default="http://localhost:8000/v1",
        help="API base URL. For vLLM use its OpenAI-compatible /v1 base, for example http://localhost:8000/v1.",
    )
    parser.add_argument(
        "--base_urls",
        nargs="+",
        default=[],
        help="One or more vLLM API base URLs. Overrides --base_url and balances requests across them.",
    )
    parser.add_argument("--api_key_env", default="VLLM_API_KEY", help="Optional API key environment variable for the vLLM endpoint.")
    parser.add_argument("--limit", type=int, default=0, help="Maximum number of rows to process after sampling. 0 means all.")
    parser.add_argument("--sample_per_platform", type=int, default=0, help="Calibration mode: sample N rows per platform.")
    parser.add_argument("--sample_per_topic", type=int, default=0, help="Calibration mode: sample N rows per topic.")
    parser.add_argument("--num_shards", type=int, default=1, help="Total number of deterministic data shards.")
    parser.add_argument("--shard_index", type=int, default=0, help="Shard index to process, from 0 to num_shards - 1.")
    parser.add_argument("--workers", type=int, default=1, help="Concurrent requests sent by this process.")
    parser.add_argument("--merge_dirs", nargs="*", default=[], help="Merge shard output directories and write final summaries.")
    parser.add_argument("--seed", type=int, default=20260703, help="Random seed for sampling.")
    parser.add_argument("--resume", action="store_true", help="Skip rows already present in the output JSONL.")
    parser.add_argument(
        "--resume_from",
        nargs="*",
        default=[],
        help="Import prior JSONL files or output directories before --resume. Current output takes precedence.",
    )
    parser.add_argument("--finalize_only", action="store_true", help="Only rebuild CSV summaries from existing JSONL.")
    parser.add_argument("--dry_run", action="store_true", help="Write prompt preview and selected input rows, but do not call the API.")
    parser.add_argument("--sleep", type=float, default=0.0, help="Seconds to sleep between API calls.")
    parser.add_argument("--max_retries", type=int, default=2, help="Retries per row on transient API errors.")
    parser.add_argument("--timeout", type=int, default=90, help="HTTP timeout seconds.")
    parser.add_argument(
        "--max_output_tokens",
        type=int,
        default=DEFAULT_MAX_OUTPUT_TOKENS,
        help=f"Maximum generated tokens per row. Default: {DEFAULT_MAX_OUTPUT_TOKENS}.",
    )
    parser.add_argument(
        "--max_post_chars",
        type=int,
        default=DEFAULT_MAX_POST_CHARS,
        help="Maximum post text characters sent to the LLM. 0 disables truncation.",
    )
    parser.add_argument(
        "--disable_thinking",
        dest="enable_thinking",
        action="store_false",
        default=True,
        help="Disable Qwen3 thinking mode. Thinking is enabled by default.",
    )
    parser.add_argument("--progress_every", type=int, default=25, help="Print and flush progress every N completed rows.")
    parser.add_argument("--skip_preflight", action="store_true", help="Skip the /models API readiness check before processing.")
    parser.add_argument("--fail_on_errors", action="store_true", help="Exit non-zero if any rows still have llm_error after processing.")
    parser.add_argument(
        "--no_retry_errors",
        action="store_true",
        help="With --resume, also skip rows that previously ended with llm_error. Default is to retry failed rows.",
    )
    parser.add_argument("--no_enforce_keep_rule", action="store_true", help="Do not override inconsistent model final_keep values.")
    return parser.parse_args()


def read_csv(path: Path) -> Tuple[List[Dict[str, str]], List[str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        rows = list(reader)
        return rows, list(reader.fieldnames or [])


def row_key(row: Dict[str, str]) -> str:
    base = "\n".join(
        [
            row.get("record_id") or row.get("mid", ""),
            row.get("platform", ""),
            row.get("topic", ""),
            row.get("keyword", ""),
            row.get("cont_clean", "")[:500],
        ]
    )
    return hashlib.sha1(base.encode("utf-8")).hexdigest()


def select_rows(rows: List[Dict[str, str]], args: argparse.Namespace) -> List[Dict[str, str]]:
    if not args.sample_per_platform and not args.sample_per_topic:
        selected = rows[:]
    else:
        rng = random.Random(args.seed)
        selected_by_key: Dict[str, Dict[str, str]] = {}

        if args.sample_per_platform:
            groups: Dict[str, List[Dict[str, str]]] = defaultdict(list)
            for row in rows:
                groups[row.get("platform", "unknown")].append(row)
            for group_rows in groups.values():
                rng.shuffle(group_rows)
                for row in group_rows[: args.sample_per_platform]:
                    selected_by_key[row_key(row)] = row

        if args.sample_per_topic:
            groups = defaultdict(list)
            for row in rows:
                groups[row.get("topic", "")].append(row)
            for group_rows in groups.values():
                rng.shuffle(group_rows)
                for row in group_rows[: args.sample_per_topic]:
                    selected_by_key[row_key(row)] = row

        selected = list(selected_by_key.values())
        selected.sort(key=lambda r: (r.get("platform", ""), r.get("topic", ""), r.get("record_id", "")))

    if args.limit and args.limit > 0:
        selected = selected[: args.limit]
    return selected


def validate_shard_args(args: argparse.Namespace) -> None:
    if args.num_shards < 1:
        raise SystemExit("--num_shards must be >= 1")
    if args.shard_index < 0 or args.shard_index >= args.num_shards:
        raise SystemExit("--shard_index must be between 0 and num_shards - 1")
    if args.workers < 1:
        raise SystemExit("--workers must be >= 1")
    if args.max_output_tokens < 1:
        raise SystemExit("--max_output_tokens must be >= 1")
    if args.progress_every < 1:
        raise SystemExit("--progress_every must be >= 1")


def apply_shard(rows: List[Dict[str, str]], args: argparse.Namespace) -> List[Dict[str, str]]:
    validate_shard_args(args)
    if args.num_shards == 1:
        return rows
    return [row for index, row in enumerate(rows) if index % args.num_shards == args.shard_index]


def processed_keys(path: Path, retry_errors: bool = True) -> set[str]:
    latest_by_key: Dict[str, Dict[str, Any]] = {}
    if not path.exists():
        return set()
    with path.open("r", encoding="utf-8-sig") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except json.JSONDecodeError:
                continue
            key = obj.get("llm_row_key")
            if key:
                latest_by_key[str(key)] = obj
    if not retry_errors:
        return set(latest_by_key.keys())
    return {
        key
        for key, obj in latest_by_key.items()
        if not str(obj.get("llm_error", "")).strip()
    }


def truncate_text(text: str, max_chars: int) -> str:
    if max_chars <= 0 or len(text) <= max_chars:
        return text
    return text[:max_chars] + "\n[TRUNCATED]"


def build_user_prompt(row: Dict[str, str], args: argparse.Namespace) -> str:
    fields = {
        "platform": row.get("platform", ""),
        "record_type": row.get("record_type", ""),
        "topic": row.get("topic", ""),
        "retrieval_keyword": row.get("keyword", ""),
        "rule_label": row.get("relevance_label", ""),
        "rule_reason": row.get("relevance_reason", ""),
        "post_text": truncate_text(row.get("cont_clean", ""), args.max_post_chars),
    }
    return (
        "Review this social media post for the assigned topic.\n"
        "Return only the structured JSON object requested by the schema.\n\n"
        + json.dumps(fields, ensure_ascii=False, indent=2)
    )


def chat_completion_payload(row: Dict[str, str], args: argparse.Namespace) -> Dict[str, Any]:
    payload: Dict[str, Any] = {
        "model": args.model,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": build_user_prompt(row, args)},
        ],
        "temperature": 0,
        "max_tokens": args.max_output_tokens,
        "response_format": {
            "type": "json_schema",
            "json_schema": {
                "name": "post_topic_stance_discussion_filter",
                "strict": True,
                "schema": OUTPUT_SCHEMA,
            },
        },
    }
    if args.enable_thinking:
        # Explicitly preserve Qwen3's reasoning mode even if the server default changes.
        payload["chat_template_kwargs"] = {"enable_thinking": True}
    return payload


def configured_base_urls(args: argparse.Namespace) -> List[str]:
    raw_urls = args.base_urls or [args.base_url]
    urls = [url.strip().rstrip("/") for url in raw_urls if url and url.strip()]
    if not urls:
        raise SystemExit("At least one non-empty vLLM base URL is required.")
    return list(dict.fromkeys(urls))


class EndpointPool:
    """Choose the least busy vLLM endpoint for each request attempt."""

    def __init__(self, base_urls: List[str]) -> None:
        self.base_urls = base_urls
        self._in_flight = [0] * len(base_urls)
        self._next_index = 0
        self._lock = threading.Lock()

    def acquire(self) -> Tuple[int, str]:
        with self._lock:
            lowest = min(self._in_flight)
            for offset in range(len(self.base_urls)):
                index = (self._next_index + offset) % len(self.base_urls)
                if self._in_flight[index] == lowest:
                    self._in_flight[index] += 1
                    self._next_index = (index + 1) % len(self.base_urls)
                    return index, self.base_urls[index]
        raise RuntimeError("No vLLM endpoint is available.")

    def release(self, index: int) -> None:
        with self._lock:
            self._in_flight[index] = max(0, self._in_flight[index] - 1)

    def in_flight_summary(self) -> str:
        with self._lock:
            return "/".join(str(count) for count in self._in_flight)


def preflight_api(args: argparse.Namespace, api_key: str, base_urls: List[str]) -> None:
    for base_url in base_urls:
        url = base_url + "/models"
        request = urllib.request.Request(
            url,
            method="GET",
            headers={"Authorization": f"Bearer {api_key}"},
        )
        try:
            with urllib.request.urlopen(request, timeout=min(args.timeout, 10)) as resp:
                resp.read()
        except Exception as exc:
            raise SystemExit(
                f"API preflight failed for {url}: {type(exc).__name__}: {exc}. "
                "Start or fix every configured vLLM service before running the filter."
            )


def strip_thinking_content(text: str) -> str:
    text = text.strip()
    if "</think>" in text:
        return text.rsplit("</think>", 1)[1].strip()
    return text


def extract_chat_output_text(response: Dict[str, Any]) -> str:
    choices = response.get("choices") or []
    if not choices:
        return ""
    message = choices[0].get("message") or {}
    content = message.get("content", "")
    if isinstance(content, str):
        # Without a reasoning parser, some vLLM versions return Qwen3 thought
        # content inline before the final JSON instead of in reasoning_content.
        return strip_thinking_content(content)
    if isinstance(content, list):
        pieces = []
        for item in content:
            if isinstance(item, dict) and isinstance(item.get("text"), str):
                pieces.append(item["text"])
        return strip_thinking_content("".join(pieces))
    return ""


def call_vllm(
    row: Dict[str, str], args: argparse.Namespace, api_key: str, endpoint_pool: EndpointPool
) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    payload = chat_completion_payload(row, args)
    sent_template_kwargs = "chat_template_kwargs" in payload
    last_error: Optional[str] = None
    for attempt in range(args.max_retries + 1):
        endpoint_index, base_url = endpoint_pool.acquire()
        url = base_url + "/chat/completions"
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        request = urllib.request.Request(
            url,
            data=body,
            method="POST",
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
        )
        try:
            try:
                with urllib.request.urlopen(request, timeout=args.timeout) as resp:
                    raw = resp.read().decode("utf-8")
                response = json.loads(raw)
                output_text = extract_chat_output_text(response)
                parsed = json.loads(output_text)
                return parsed, response
            except urllib.error.HTTPError as exc:
                detail = exc.read().decode("utf-8", errors="replace")
                last_error = f"HTTP {exc.code}: {detail[:1000]}"
                if exc.code == 400 and sent_template_kwargs:
                    # Older vLLM versions may not accept this non-OpenAI extension.
                    payload.pop("chat_template_kwargs", None)
                    sent_template_kwargs = False
                    continue
                if exc.code not in {408, 409, 429, 500, 502, 503, 504}:
                    break
            except json.JSONDecodeError as exc:
                last_error = f"{type(exc).__name__}: {exc}"
                # Repeating a deterministic malformed completion wastes several timeout windows.
                break
            except (urllib.error.URLError, TimeoutError) as exc:
                last_error = f"{type(exc).__name__}: {exc}"
        finally:
            endpoint_pool.release(endpoint_index)
        if attempt < args.max_retries:
            time.sleep(min(60, 2**attempt))
    raise RuntimeError(last_error or "Unknown API error")


def enforced_keep(parsed: Dict[str, Any]) -> bool:
    return (
        parsed.get("topic_relevance") in KEEP_TOPIC_RELEVANCE
        and bool(parsed.get("has_stance")) is True
        and parsed.get("discussion_potential") in KEEP_DISCUSSION
    )


def usage_fields(response: Dict[str, Any]) -> Dict[str, int]:
    usage = response.get("usage") or {}
    input_tokens = int(usage.get("input_tokens") or usage.get("prompt_tokens") or 0)
    output_tokens = int(usage.get("output_tokens") or usage.get("completion_tokens") or 0)
    total_tokens = int(usage.get("total_tokens") or input_tokens + output_tokens)
    return {
        "llm_input_tokens": input_tokens,
        "llm_output_tokens": output_tokens,
        "llm_total_tokens": total_tokens,
    }


def result_row(
    row: Dict[str, str],
    parsed: Optional[Dict[str, Any]],
    response: Optional[Dict[str, Any]],
    args: argparse.Namespace,
    error: str = "",
) -> Dict[str, Any]:
    key = row_key(row)
    out: Dict[str, Any] = dict(row)
    out["llm_row_key"] = key
    out["llm_model"] = args.model
    out["llm_processed_at"] = datetime.now().isoformat(timespec="seconds")
    out["llm_error"] = error

    if parsed is None:
        out.update({field: "" for field in LLM_FIELDS if field not in out})
        out["llm_final_keep"] = "false"
        out["llm_needs_human_review"] = "true"
        return out

    model_keep = bool(parsed.get("final_keep"))
    final_keep = model_keep if args.no_enforce_keep_rule else enforced_keep(parsed)
    needs_review = bool(parsed.get("needs_human_review")) or parsed.get("topic_relevance") == "insufficient_context"
    needs_review = needs_review or parsed.get("discussion_potential") == "unclear"

    out.update(
        {
            "llm_topic_relevance": parsed.get("topic_relevance", ""),
            "llm_relevance_reason": parsed.get("relevance_reason", ""),
            "llm_has_stance": str(bool(parsed.get("has_stance"))).lower(),
            "llm_stance_label": parsed.get("stance_label", ""),
            "llm_stance_target": parsed.get("stance_target", ""),
            "llm_stance_reason": parsed.get("stance_reason", ""),
            "llm_discussion_potential": parsed.get("discussion_potential", ""),
            "llm_discussion_reason": parsed.get("discussion_reason", ""),
            "llm_final_keep_model": str(model_keep).lower(),
            "llm_final_keep": str(final_keep).lower(),
            "llm_final_keep_reason": parsed.get("final_keep_reason", ""),
            "llm_confidence": parsed.get("confidence", ""),
            "llm_needs_human_review": str(needs_review).lower(),
            "llm_response_id": (response or {}).get("id", ""),
        }
    )
    out.update(usage_fields(response or {}))
    return out


def read_jsonl(path: Path) -> List[Dict[str, Any]]:
    if not path.exists():
        return []
    rows: List[Dict[str, Any]] = []
    with path.open("r", encoding="utf-8-sig") as f:
        for line in f:
            if not line.strip():
                continue
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return rows


def write_jsonl(path: Path, rows: List[Dict[str, Any]]) -> None:
    with path.open("w", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")


def write_csv(path: Path, rows: List[Dict[str, Any]], fields: List[str]) -> None:
    with path.open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def dedupe_latest_rows(rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    latest_by_key: Dict[str, Dict[str, Any]] = {}
    key_order: List[str] = []
    for row in rows:
        key = str(row.get("llm_row_key") or row_key(row))
        if not key:
            continue
        if key not in latest_by_key:
            key_order.append(key)
        latest_by_key[key] = row
    return [latest_by_key[key] for key in key_order]


def error_rows(rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    return [r for r in rows if str(r.get("llm_error", "")).strip()]


def summarize(rows: List[Dict[str, Any]], output_dir: Path) -> None:
    rows = dedupe_latest_rows(rows)
    keep_rows = [r for r in rows if str(r.get("llm_final_keep", "")).lower() == "true"]
    removed_rows = [r for r in rows if str(r.get("llm_final_keep", "")).lower() != "true"]
    failed_rows = error_rows(rows)
    success_rows = [r for r in rows if not str(r.get("llm_error", "")).strip()]
    review_rows = [
        r
        for r in rows
        if str(r.get("llm_needs_human_review", "")).lower() == "true" or r.get("llm_error")
    ]

    original_fields = [field for field in rows[0].keys() if field not in LLM_FIELDS] if rows else []
    fields = original_fields + [field for field in LLM_FIELDS if field not in original_fields]
    write_csv(output_dir / "llm_post_relevance_pairs.csv", rows, fields)
    write_csv(output_dir / "llm_post_relevance_filtered.csv", keep_rows, fields)
    write_csv(output_dir / "llm_post_relevance_removed.csv", removed_rows, fields)
    write_csv(output_dir / "llm_post_relevance_review.csv", review_rows, fields)
    write_csv(output_dir / "llm_post_relevance_errors.csv", failed_rows, fields)

    platform_summary = build_group_summary(rows, "platform")
    topic_summary = build_group_summary(rows, "topic")
    write_csv(output_dir / "llm_platform_summary.csv", platform_summary, list(platform_summary[0].keys()) if platform_summary else [])
    write_csv(output_dir / "llm_topic_summary.csv", topic_summary, list(topic_summary[0].keys()) if topic_summary else [])

    label_summary = {
        "processed_rows": len(rows),
        "success_rows": len(success_rows),
        "error_rows": len(failed_rows),
        "error_rate": round(len(failed_rows) / len(rows), 4) if rows else 0,
        "kept_rows": len(keep_rows),
        "removed_rows": len(removed_rows),
        "review_rows": len(review_rows),
        "error_counts": dict(Counter(str(r.get("llm_error", "")) for r in failed_rows)),
        "topic_relevance_counts": dict(Counter(str(r.get("llm_topic_relevance", "")) for r in rows)),
        "stance_label_counts": dict(Counter(str(r.get("llm_stance_label", "")) for r in rows)),
        "discussion_potential_counts": dict(Counter(str(r.get("llm_discussion_potential", "")) for r in rows)),
        "platform_counts": dict(Counter(str(r.get("platform", "")) for r in rows)),
        "total_input_tokens": sum(int(r.get("llm_input_tokens") or 0) for r in rows),
        "total_output_tokens": sum(int(r.get("llm_output_tokens") or 0) for r in rows),
        "total_tokens": sum(int(r.get("llm_total_tokens") or 0) for r in rows),
    }
    (output_dir / "llm_run_summary.json").write_text(
        json.dumps(label_summary, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def build_group_summary(rows: List[Dict[str, Any]], group_field: str) -> List[Dict[str, Any]]:
    grouped: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    for row in rows:
        grouped[str(row.get(group_field, ""))].append(row)

    summary_rows = []
    for group, group_rows in grouped.items():
        keep_count = sum(str(r.get("llm_final_keep", "")).lower() == "true" for r in group_rows)
        error_count = sum(bool(str(r.get("llm_error", "")).strip()) for r in group_rows)
        stance_count = sum(str(r.get("llm_has_stance", "")).lower() == "true" for r in group_rows)
        discussion_count = sum(r.get("llm_discussion_potential") in KEEP_DISCUSSION for r in group_rows)
        summary_rows.append(
            {
                group_field: group,
                "row_count": len(group_rows),
                "success_count": len(group_rows) - error_count,
                "error_count": error_count,
                "error_rate": round(error_count / len(group_rows), 4) if group_rows else 0,
                "llm_keep_count": keep_count,
                "llm_keep_rate": round(keep_count / len(group_rows), 4) if group_rows else 0,
                "has_stance_count": stance_count,
                "has_stance_rate": round(stance_count / len(group_rows), 4) if group_rows else 0,
                "medium_high_discussion_count": discussion_count,
                "medium_high_discussion_rate": round(discussion_count / len(group_rows), 4) if group_rows else 0,
                "strongly_relevant_count": sum(r.get("llm_topic_relevance") == "strongly_relevant" for r in group_rows),
                "relevant_count": sum(r.get("llm_topic_relevance") == "relevant" for r in group_rows),
                "weak_keyword_match_count": sum(r.get("llm_topic_relevance") == "weak_keyword_match" for r in group_rows),
                "off_topic_count": sum(r.get("llm_topic_relevance") == "off_topic" for r in group_rows),
                "insufficient_context_count": sum(r.get("llm_topic_relevance") == "insufficient_context" for r in group_rows),
            }
        )
    return sorted(summary_rows, key=lambda r: r["llm_keep_count"], reverse=True)


def merge_shard_outputs(shard_dirs: List[Path], output_dir: Path) -> List[Dict[str, Any]]:
    merged_by_key: Dict[str, Dict[str, Any]] = {}
    input_files: List[str] = []
    for shard_dir in shard_dirs:
        jsonl_path = shard_dir / "llm_post_relevance_pairs.jsonl" if shard_dir.is_dir() else shard_dir
        input_files.append(str(jsonl_path))
        for row in read_jsonl(jsonl_path):
            key = str(row.get("llm_row_key") or row_key(row))
            if key:
                merged_by_key[key] = row

    rows = list(merged_by_key.values())
    rows.sort(key=lambda r: (r.get("platform", ""), r.get("topic", ""), r.get("record_id", ""), r.get("llm_row_key", "")))
    output_dir.mkdir(parents=True, exist_ok=True)
    write_jsonl(output_dir / "llm_post_relevance_pairs.jsonl", rows)
    summarize(rows, output_dir)
    merge_meta = {
        "merged_rows": len(rows),
        "input_files": input_files,
        "merged_at": datetime.now().isoformat(timespec="seconds"),
    }
    (output_dir / "llm_merge_summary.json").write_text(
        json.dumps(merge_meta, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(f"Merged {len(rows)} rows into {output_dir}")
    return rows


def import_resume_rows(jsonl_path: Path, sources: List[str]) -> Tuple[int, List[str]]:
    """Import prior outputs once, while allowing this run's own results to win."""
    source_files: List[Path] = []
    target = jsonl_path.resolve()
    for source in sources:
        source_path = Path(source)
        source_file = source_path / "llm_post_relevance_pairs.jsonl" if source_path.is_dir() else source_path
        if not source_file.exists():
            print(f"Skipping missing resume source: {source_file}")
            continue
        if source_file.resolve() == target:
            continue
        source_files.append(source_file)

    if not source_files:
        return 0, []

    imported_rows: List[Dict[str, Any]] = []
    for source_file in source_files:
        imported_rows.extend(read_jsonl(source_file))

    # Older shard results are read first; the current dynamic run overrides them.
    combined_rows = dedupe_latest_rows(imported_rows + read_jsonl(jsonl_path))
    write_jsonl(jsonl_path, combined_rows)
    return len(combined_rows), [str(path) for path in source_files]


def write_prompt_preview(output_dir: Path, rows: List[Dict[str, str]], args: argparse.Namespace) -> None:
    preview = {
        "model": args.model,
        "base_url": args.base_url,
        "base_urls": configured_base_urls(args),
        "system_prompt": SYSTEM_PROMPT,
        "schema": OUTPUT_SCHEMA,
        "sample_count": len(rows),
        "first_user_prompt": build_user_prompt(rows[0], args) if rows else "",
    }
    (output_dir / "llm_prompt_preview.json").write_text(json.dumps(preview, ensure_ascii=False, indent=2), encoding="utf-8")
    if rows:
        fields = list(rows[0].keys())
        write_csv(output_dir / "llm_selected_input_preview.csv", rows, fields)


def process_row(
    row: Dict[str, str], args: argparse.Namespace, api_key: str, endpoint_pool: EndpointPool
) -> Dict[str, Any]:
    key = row_key(row)
    try:
        parsed, response = call_vllm(row, args, api_key, endpoint_pool)
        out = result_row(row, parsed, response, args)
    except Exception as exc:
        out = result_row(row, None, None, args, error=str(exc))
    out["llm_row_key"] = key
    return out


def run_rows(
    todo: List[Dict[str, str]], jsonl_path: Path, args: argparse.Namespace, api_key: str, endpoint_pool: EndpointPool
) -> None:
    """Keep a fixed number of requests in flight until the shared queue is empty."""
    if not todo:
        return

    worker_count = min(args.workers, len(todo))
    completed_count = 0
    success_count = 0
    error_count = 0
    started_at = time.monotonic()
    iterator = iter(todo)

    def submit_next(executor: ThreadPoolExecutor, pending: set[Future[Dict[str, Any]]]) -> bool:
        try:
            row = next(iterator)
        except StopIteration:
            return False
        pending.add(executor.submit(process_row, row, args, api_key, endpoint_pool))
        return True

    with jsonl_path.open("a", encoding="utf-8", buffering=1024 * 1024) as output_file:
        with ThreadPoolExecutor(max_workers=worker_count) as executor:
            pending: set[Future[Dict[str, Any]]] = set()
            for _ in range(worker_count):
                submit_next(executor, pending)

            while pending:
                finished, pending = wait(pending, return_when=FIRST_COMPLETED)
                for future in finished:
                    out = future.result()
                    output_file.write(json.dumps(out, ensure_ascii=False) + "\n")
                    completed_count += 1
                    if str(out.get("llm_error", "")).strip():
                        error_count += 1
                    else:
                        success_count += 1

                    submit_next(executor, pending)

                    if completed_count % args.progress_every == 0 or completed_count == 1:
                        output_file.flush()
                        elapsed_minutes = max((time.monotonic() - started_at) / 60, 1e-9)
                        rate = completed_count / elapsed_minutes
                        print(
                            f"processed {completed_count}/{len(todo)} | "
                            f"ok {success_count}, errors {error_count} | "
                            f"{rate:.1f} rows/min | workers {len(pending)}/{worker_count} | "
                            f"endpoints {endpoint_pool.in_flight_summary()}"
                        )
                    if args.sleep:
                        time.sleep(args.sleep)
        output_file.flush()


def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(line_buffering=True)
    args = parse_args()
    input_path = Path(args.input)
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    jsonl_path = output_dir / "llm_post_relevance_pairs.jsonl"

    if args.merge_dirs:
        merged_rows = merge_shard_outputs([Path(item) for item in args.merge_dirs], output_dir)
        failed_count = len(error_rows(merged_rows))
        if args.fail_on_errors and failed_count:
            raise SystemExit(f"{failed_count} merged rows still have llm_error.")
        return

    if args.finalize_only:
        rows = read_jsonl(jsonl_path)
        if not rows:
            raise SystemExit(f"No rows found in {jsonl_path}")
        summarize(rows, output_dir)
        print(f"Finalized {len(rows)} rows from {jsonl_path}")
        return

    rows, _fields = read_csv(input_path)
    selected = select_rows(rows, args)
    selected_before_shard = len(selected)
    selected = apply_shard(selected, args)
    write_prompt_preview(output_dir, selected[: min(len(selected), 100)], args)

    if args.dry_run:
        print(f"Dry run only. Selected rows: {len(selected)}")
        print(f"Wrote prompt preview to {output_dir / 'llm_prompt_preview.json'}")
        return

    api_key = os.environ.get(args.api_key_env) or "EMPTY"

    imported_count, imported_sources = import_resume_rows(jsonl_path, args.resume_from)

    done = processed_keys(jsonl_path, retry_errors=not args.no_retry_errors) if args.resume else set()
    todo = [row for row in selected if row_key(row) not in done]
    print(f"Input rows: {len(rows)}")
    print(f"Selected rows before shard: {selected_before_shard}")
    print(f"Shard: {args.shard_index}/{args.num_shards}")
    print(f"Selected rows in shard: {len(selected)}")
    print(f"Workers: {args.workers}")
    print(f"Qwen3 thinking: {'enabled' if args.enable_thinking else 'disabled'}")
    print(f"Max output tokens: {args.max_output_tokens}")
    if imported_sources:
        print(f"Imported {imported_count} rows from {len(imported_sources)} resume source(s)")
    print(f"Already processed: {len(done)}")
    print(f"To process: {len(todo)}")
    print(f"Output JSONL: {jsonl_path}")

    base_urls = configured_base_urls(args)
    endpoint_pool = EndpointPool(base_urls)
    print(f"Endpoints: {', '.join(base_urls)}")

    if todo and not args.skip_preflight:
        preflight_api(args, api_key, base_urls)

    run_rows(todo, jsonl_path, args, api_key, endpoint_pool)

    all_rows = dedupe_latest_rows(read_jsonl(jsonl_path))
    write_jsonl(jsonl_path, all_rows)
    summarize(all_rows, output_dir)
    print(f"Done. Processed rows in JSONL: {len(all_rows)}")
    print(f"Filtered: {output_dir / 'llm_post_relevance_filtered.csv'}")
    failed_count = len(error_rows(all_rows))
    if args.fail_on_errors and failed_count:
        raise SystemExit(
            f"{failed_count} rows still have llm_error. Fix the service issue and rerun with --resume to retry them."
        )


if __name__ == "__main__":
    main()
