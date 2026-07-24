# Other-platform comment preparation

This directory prepares the earlier `socialmedia_data` comment snapshot for the
next Qwen relevance-annotation stage without modifying any source file.

## Annotation unit

One output row is:

```text
one comment x one retained post/video x one topic
```

Comments are evaluated independently. `parent_comment_id` is preserved when a
source value exists, but the current matched Instagram and YouTube data has no
usable parent-comment IDs. Do not call these rows threads.

## Inputs

- Current retained posts/videos:
  `../post_filter/output/qwen32b_8gpu/merged/llm_post_relevance_filtered.csv`
- Earlier cleaned cross-platform messages (read-only):
  `../../../socialmedia_data/clean_outputs/messages_clean.jsonl`
- Raw comment files are scanned only to report empty/non-empty source counts.

## Outputs

- `output/matched_comments_for_annotation.csv`: UTF-8-BOM tabular input for
  review and later annotation.
- `output/matched_comments_for_annotation.jsonl`: the same rows for server-side
  streaming, resume, and batch inference.
- `output/matched_comments_for_annotation.xlsx`: formatted human-review copy.
- `output/comment_preparation_summary.csv`: compact per-platform audit table.
- `output/comment_preparation_summary.json`: paths, fingerprints, totals, join
  diagnostics, and source-state verification.

The CSV/JSONL contain source/context fields only. A later annotation runner
should write a separate derived result with these three model fields:

```text
topic_relevance
training_grade
annotation_reason
```

## Matching rules

- Instagram: normalized comment `r_url` -> retained post `url`.
- YouTube: comment `r_mid` (original `r_vid`) -> retained video `native_id`.
- Reddit: the supplied raw comments have empty text, so no annotation rows are
  emitted.
- X: the supplied snapshot has no comment source file.

Duplicate retained keywords are collapsed within the same
`post + topic`. A stable `annotation_row_id` is computed from
`platform + post_mid + topic + comment_mid`.

## Run

From `D:\social_network\benchmark\data_clean\pipelines\other_platform_comments`:

```powershell
python scripts/prepare_other_platform_comments.py
```

The script writes through temporary files and atomically replaces only its own
CSV/JSONL outputs. It records source size and modification time before and after
the run and stops if a source changes during processing.
