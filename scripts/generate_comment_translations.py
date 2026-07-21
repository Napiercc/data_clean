#!/usr/bin/env python3
"""Translate sampled retained posts and all of their matched comments to Chinese."""

from __future__ import annotations

import argparse
from hashlib import sha256
import json
from pathlib import Path
import re
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_PAIRS = ROOT / "output/qwen32b_8gpu/merged/llm_post_relevance_pairs.csv"
DEFAULT_MESSAGES = ROOT.parent / "socialmedia_data/clean_outputs/messages_clean.jsonl"
DEFAULT_OUTPUT = ROOT / "output/qwen32b_8gpu/merged/comment_match_translations.json"
DEFAULT_MODEL = "Helsinki-NLP/opus-mt-en-zh"
MANUAL_TRANSLATION_OVERRIDES = {
    "comments": {
        "YouTube:comment:Ugx2tRKTCpV2ahHozeZ4AaABAg": (
            "希望给孩子吃的不辣，给大人吃的辣一些；仅此就足以让大人感到开心。"
        )
    }
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Translate the sampled retained posts and matched comments with a local model."
    )
    parser.add_argument("--pairs", type=Path, default=DEFAULT_PAIRS, help="Merged post-topic pairs CSV.")
    parser.add_argument("--messages", type=Path, default=DEFAULT_MESSAGES, help="Cleaned messages JSONL.")
    parser.add_argument(
        "--audit-per-platform",
        type=int,
        default=12,
        help="Number of distinct retained posts sampled per platform.",
    )
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT, help="Translation cache JSON.")
    parser.add_argument("--model", default=DEFAULT_MODEL, help="Hugging Face translation model.")
    parser.add_argument("--batch-size", type=int, default=8, help="Translation chunks per model batch.")
    parser.add_argument("--save-every", type=int, default=8, help="Source records translated per checkpoint.")
    parser.add_argument("--max-chunk-chars", type=int, default=450, help="Maximum source characters per chunk.")
    parser.add_argument("--force", action="store_true", help="Regenerate existing cached translations.")
    parser.add_argument(
        "--local-files-only",
        action="store_true",
        help="Do not download the model when it is missing from the local Hugging Face cache.",
    )
    return parser.parse_args()


def normalise_text(value: str) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def needs_model_translation(value: str) -> bool:
    clean = normalise_text(value)
    if not clean or re.fullmatch(r"https?://\S+", clean) or re.fullmatch(r"\d{1,2}:\d{2}", clean):
        return False
    return any(character.isalpha() for character in clean)


def split_text(text: str, limit: int) -> list[str]:
    """Split without dropping text, preferring sentence and word boundaries."""

    clean = normalise_text(text)
    if not clean:
        return []
    if len(clean) <= limit:
        return [clean]
    sentences = re.split(r"(?<=[.!?。！？])\s+", clean)
    chunks: list[str] = []
    current = ""
    for sentence in sentences:
        sentence = sentence.strip()
        if not sentence:
            continue
        if len(sentence) > limit:
            words = sentence.split()
        else:
            words = [sentence]
        for word in words:
            if len(word) > limit:
                if current:
                    chunks.append(current)
                    current = ""
                chunks.extend(word[index : index + limit] for index in range(0, len(word), limit))
                continue
            candidate = f"{current} {word}".strip()
            if current and len(candidate) > limit:
                chunks.append(current)
                current = word
            else:
                current = candidate
    if current:
        chunks.append(current)
    return chunks


def load_cache(
    path: Path,
    model_name: str,
    source_description: str,
    source_sha256: str,
) -> dict[str, Any]:
    if path.exists():
        cache = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(cache, dict):
            raise SystemExit(f"Translation cache must be a JSON object: {path}")
    else:
        cache = {}
    cache["model"] = model_name
    cache["source_file"] = source_description
    cache["source_sha256"] = source_sha256
    cache.setdefault("posts", {})
    cache.setdefault("comments", {})
    return cache


def write_cache(path: Path, cache: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(cache, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


def collect_sources(rows: list[dict[str, str]]) -> list[tuple[str, str, str]]:
    sources: list[tuple[str, str, str]] = []
    seen_posts: set[str] = set()
    seen_comments: set[str] = set()
    for row in rows:
        post_mid = row.get("post_mid", "").strip()
        if post_mid and post_mid not in seen_posts:
            seen_posts.add(post_mid)
            sources.append(("posts", post_mid, row.get("post_text", "")))
        comment_mid = row.get("comment_mid", "").strip()
        if comment_mid and comment_mid not in seen_comments:
            seen_comments.add(comment_mid)
            sources.append(("comments", comment_mid, row.get("comment_text", "")))
    return sources


def main() -> None:
    args = parse_args()
    if not args.pairs.exists():
        raise SystemExit(f"Pairs CSV not found: {args.pairs}")
    if not args.messages.exists():
        raise SystemExit(f"Cleaned messages JSONL not found: {args.messages}")
    if args.audit_per_platform < 0:
        raise SystemExit("--audit-per-platform must be >= 0")
    if args.batch_size < 1 or args.save_every < 1 or args.max_chunk_chars < 100:
        raise SystemExit("Batch sizes must be >= 1 and --max-chunk-chars must be >= 100.")

    try:
        import torch
        from transformers import AutoModelForSeq2SeqLM, AutoTokenizer
    except ImportError as exc:
        raise SystemExit(
            "This script requires torch, transformers, and sentencepiece. "
            "Run it with the configured pytorch Conda environment."
        ) from exc

    from generate_llm_summary_report import build_comment_report, dedupe_rows, read_csv

    comment_report = build_comment_report(
        dedupe_rows(read_csv(args.pairs)),
        args.messages,
        args.audit_per_platform,
        {"posts": {}, "comments": {}},
        require_translations=False,
    )
    sources = collect_sources(comment_report["audit_rows"])
    source_sha256 = sha256(
        json.dumps(sources, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    cache = load_cache(
        args.output,
        args.model,
        f"{args.pairs} + {args.messages}",
        source_sha256,
    )
    for kind, values in MANUAL_TRANSLATION_OVERRIDES.items():
        cache[kind].update(values)
    for kind, item_id, source_text in sources:
        if not needs_model_translation(source_text):
            cache[kind][item_id] = normalise_text(source_text)
    pending = [
        item
        for item in sources
        if needs_model_translation(item[2])
        and item[1] not in MANUAL_TRANSLATION_OVERRIDES.get(item[0], {})
        and (args.force or not str(cache[item[0]].get(item[1], "")).strip())
    ]
    print(
        f"Sources: {len(sources):,}; cached: {len(sources) - len(pending):,}; "
        f"pending: {len(pending):,}"
    )
    if not pending:
        write_cache(args.output, cache)
        print(f"Translation cache is complete: {args.output}")
        return

    tokenizer = AutoTokenizer.from_pretrained(
        args.model,
        local_files_only=args.local_files_only,
    )
    model = AutoModelForSeq2SeqLM.from_pretrained(
        args.model,
        local_files_only=args.local_files_only,
    )
    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    model.to(device)
    model.eval()
    print(f"Translation device: {device}")

    completed = 0
    for start in range(0, len(pending), args.save_every):
        group = pending[start : start + args.save_every]
        source_chunks: list[str] = []
        chunk_counts: list[int] = []
        for _, _, source_text in group:
            chunks = split_text(source_text, args.max_chunk_chars)
            source_chunks.extend(chunks)
            chunk_counts.append(len(chunks))

        translated_chunks: list[str] = []
        for batch_start in range(0, len(source_chunks), args.batch_size):
            batch = source_chunks[batch_start : batch_start + args.batch_size]
            encoded = tokenizer(
                batch,
                return_tensors="pt",
                padding=True,
                truncation=True,
                max_length=512,
            ).to(device)
            max_input_tokens = int(encoded["attention_mask"].sum(dim=1).max().item())
            max_target_tokens = min(384, max(24, max_input_tokens * 2 + 16))
            with torch.inference_mode():
                generated = model.generate(
                    **encoded,
                    max_new_tokens=max_target_tokens,
                    num_beams=4,
                    early_stopping=True,
                    no_repeat_ngram_size=3,
                    repetition_penalty=1.15,
                    do_sample=False,
                )
            translated_chunks.extend(
                tokenizer.batch_decode(generated, skip_special_tokens=True)
            )

        cursor = 0
        for (kind, item_id, source_text), chunk_count in zip(group, chunk_counts):
            translation = " ".join(
                normalise_text(value)
                for value in translated_chunks[cursor : cursor + chunk_count]
                if normalise_text(value)
            ).strip()
            cursor += chunk_count
            if normalise_text(source_text) and not translation:
                raise RuntimeError(f"Empty translation returned for {kind}:{item_id}")
            if len(source_text) <= 200 and len(translation) > max(160, len(source_text) * 6):
                raise RuntimeError(f"Pathologically long translation for {kind}:{item_id}")
            cache[kind][item_id] = translation
            completed += 1
        write_cache(args.output, cache)
        print(f"Translated {completed:,}/{len(pending):,} pending records")

    print(
        f"Wrote {args.output} with {len(cache['posts']):,} posts and "
        f"{len(cache['comments']):,} comments"
    )


if __name__ == "__main__":
    main()
