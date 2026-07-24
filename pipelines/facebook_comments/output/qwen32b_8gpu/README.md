# Annotation Run Output

Status: **BLOCKED**

## Current counts

- Selected rows: 3505
- Successful annotations: 3303
- Unresolved errors: 202
- Review workbook: `results/facebook_comments_annotation_review_three_fields.xlsx`
- Final workbook: not written yet

## Start here

- `results/facebook_comments_annotation_review_three_fields.xlsx`: all original A:P rows plus Q:S (`topic_relevance`, `training_grade`, `annotation_reason`); 202 unresolved rows are blank
- `results/facebook_selected_commenters_for_crawl.csv`: relevant successful rows expanded to one `commenter_id + post_mid + thread_id` relation per row for downstream crawling
- `results/annotations.csv`: original ten-field v2 successful annotations retained for audit
- `results/errors.csv`: unresolved rows requiring repair
- `results/run_summary.json`: counts, labels, attempts, speed, and integrity status
- `final/`: completed annotated workbook after all required rows pass

## Internal folders

- `state/`: resume database; do not edit or delete while work is incomplete
- `audit/`: manifests, validation reports, JSONL exports, and attempt history
- `logs/`: terminal and vLLM logs

`FINALIZATION_BLOCKED.json` exists at the run root only while unresolved errors prevent final workbook creation.
