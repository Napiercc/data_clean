# Annotation Run Output

Status: **COMPLETE**

## Current counts

- Selected rows: 222
- Successful annotations: 222
- Combined successful annotations: 3505
- Unresolved errors: 0
- Review workbook: `results/facebook_comments_annotation_review.xlsx`
- Final workbook: `final/facebook_comments_comprehensive_annotated.xlsx`

## Start here

- `results/facebook_comments_annotation_review.xlsx`: original A:P plus three AI fields; failed rows remain blank
- `results/annotations.csv`: original row data and successful annotations in one CSV
- `results/facebook_selected_commenters_for_crawl.csv`: relevant commenter relations for downstream crawling
- `results/errors.csv`: original row data and unresolved error details in one CSV
- `results/run_summary.json`: counts, labels, attempts, speed, and integrity status
- `final/`: completed annotated workbook after all required rows pass

## Internal folders

- `state/`: resume database; do not edit or delete while work is incomplete
- `audit/`: manifests, validation reports, JSONL exports, and attempt history
- `logs/`: terminal and vLLM logs

`FINALIZATION_BLOCKED.json` exists at the run root only while unresolved errors prevent final workbook creation.
