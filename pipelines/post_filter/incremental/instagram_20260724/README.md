# Instagram incremental post filtering

This directory is an isolated incremental run for the Instagram files collected
in `E:\Users\ASUS\OneDrive\Desktop\ig_data`.

Processing order:

1. Normalize the raw Instagram files with the existing multi-platform cleaner.
2. Keep Instagram posts only.
3. Deduplicate against historical rule-filter rows using `mid + topic`.
4. Apply the unchanged rule filter.
5. Write the rule-kept rows as a separate Qwen3-32B input file.

The historical source files and existing post-filter outputs are read-only
references and are not modified.

## Local preparation

From this directory:

```powershell
python .\prepare_instagram_incremental.py
```

Review:

- `manifest.json`: counts, versions, source hashes, and output paths.
- `00_audit/historical_mid_topic_pairs_excluded.csv`: rows skipped because the
  same `mid + topic` was already processed historically.
- `00_audit/new_batch_posts_excluded_during_normalization.csv`: new raw post
  records excluded by objective normalization, including empty-text rows.
- `01_deduplicated/instagram_incremental_posts.csv`: normalized rows after
  cross-batch deduplication.
- `02_rule_filter/instagram_incremental_post_relevance_report.html`: rule-stage
  report.
- `02_rule_filter/instagram_incremental_post_manual_review_sample.csv`: sample
  for manual inspection.
- `03_qwen_input/instagram_incremental_qwen_input.csv`: rows to send to Qwen.

## Qwen3-32B on the 8-GPU H20 server

Start the existing vLLM services first, then run:

```bash
bash incremental/instagram_20260724/run_qwen_8gpu.sh
```

The new model output is isolated under:

```text
incremental/instagram_20260724/04_qwen_output/
```
