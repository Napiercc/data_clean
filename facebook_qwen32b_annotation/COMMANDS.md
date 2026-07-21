# Eight-H20 Run Commands

Run every command from the package root:

```bash
cd /path/to/facebook_qwen32b_annotation
```

## 1. Check the environment and frozen inputs

```bash
python --version
python -c "import openpyxl; print(openpyxl.__version__)"
python -c "import vllm; print(vllm.__version__)"
nvidia-smi
scripts/validate_inputs.sh
```

Input validation must confirm 3,505 workbook rows and the frozen hashes recorded in `input/input_manifest.json` and `input/SHA256SUMS.txt`. Stop if validation fails. Do not substitute the current raw scraper export for the bundled snapshot.

This is the all-English v4 three-field annotation contract. One input row is one root comment plus its replies; separate root comments under the same post remain separate rows. The model returns only `topic_relevance`, `training_grade`, and `annotation_reason`. Do not resume or merge an earlier task database into the v4 run directory.

## 2. Start two TP4 services

The default model location is `../../models/Qwen3-32B` from this package, which resolves to the same `benchmark/models/Qwen3-32B` directory used by the existing post-cleaning scripts:

```bash
scripts/start_vllm_8gpu_qwen32b.sh
```

If the model or Python environment is elsewhere:

```bash
MODEL_PATH=/data/models/Qwen3-32B \
PYTHON_BIN=/opt/venvs/qwen/bin/python \
scripts/start_vllm_8gpu_qwen32b.sh
```

The startup script creates the replicas sequentially:

- `CUDA_VISIBLE_DEVICES=0,1,2,3`, port 8000, TP=4
- `CUDA_VISIBLE_DEVICES=4,5,6,7`, port 8001, TP=4

It waits for the first endpoint to become healthy before loading the second replica, reducing the peak host-memory pressure caused by concurrent model loading. PID files are written to `run/`, and server logs are written to `logs/vllm/`.

The launcher follows the post-cleaning method: two TP4 replicas of the local Qwen3-32B weights through vLLM's OpenAI-compatible API, with `dtype=auto`. It deliberately uses `MAX_MODEL_LEN=32768` instead of the post cleaner's 4096 because comment threads are substantially longer. Stop any old post-cleaning services on ports 8000 and 8001 before running this launcher; do not reuse their 4096-token processes.

Health checks:

```bash
curl -fsS http://127.0.0.1:8000/v1/models
curl -fsS http://127.0.0.1:8001/v1/models
```

The annotation client also checks that both responses advertise `Qwen3-32B`; mere HTTP availability is not sufficient. This prevents an older post-cleaning service or another model on the same ports from passing preflight accidentally.

Monitoring:

```bash
watch -n 2 nvidia-smi
tail -f logs/vllm/vllm-8000.log
tail -f logs/vllm/vllm-8001.log
```

## 3. Run the 64-row sample

```bash
scripts/run_sample_8gpu_vllm.sh
```

Defaults: 16 workers, a 180-second timeout, five network retries, two semantic retries, 768 output tokens, and thinking disabled. The 64-row selection and seed are fixed for reproducible review.

Review these sample artifacts:

```text
output/qwen32b_8gpu_sample_v4_three_fields/
|-- README.md
|-- results/facebook_comments_annotation_review.xlsx
|-- final/facebook_comments_sample_annotated.xlsx
|-- results/run_summary.json
|-- results/annotations.csv
|-- results/errors.csv
|-- state/tasks.sqlite
`-- audit/run_manifest.json
```

Start with the run's `README.md`. Treat `audit/run_manifest.json` as authoritative for exact inputs and settings.

## 4. Run or resume the full dataset

```bash
scripts/run_full_8gpu_vllm.sh
```

The script includes `--resume`. After an interruption, confirm that both API endpoints are healthy and run the same command again. Successful v4 tasks are reused and failed v4 tasks are retried.

Do not place an old-contract `tasks.sqlite`, JSONL file, CSV, or workbook in the v4 output directory. The current resume database is `state/tasks.sqlite`; earlier and v4 task state are intentionally incompatible.

Inspect progress and errors:

```bash
tail -f output/qwen32b_8gpu_v4_three_fields/logs/annotate.log
```

The terminal and log show the same progress fields. For example:

```text
PROGRESS overall=1250/3505 percent=35.7% current=250/2505 new_successes=248 new_failures=2 rate=0.500 rows/s elapsed=00:08:20 eta=01:15:10
```

- `overall` and `percent` include successes restored by `--resume`.
- `current` counts rows completed by the current invocation out of its pending rows.
- `rate` is the current invocation's cumulative average throughput.
- `eta` uses that average rate and may fluctuate early; it is `unknown` before the first usable rate.
- The sample script reports every 10 rows. The full script reports every 50 rows unless `PROGRESS_EVERY` is set.

If the full-run script exits nonzero because of `--fail-on-errors`, inspect `results/errors.csv` and `audit/attempts.jsonl`. The review workbook is still written with A:P and any successful Q:S annotations together; failed rows remain blank. Only the workbook under `final/` represents a completed full result.

## 5. Repair the preserved v2 failures

Use this instead of rerunning all 3,505 rows when the organized legacy files remain at `output/qwen32b_8gpu/results/`:

```bash
scripts/retry_legacy_errors_8gpu_vllm.sh
```

The launcher:

- reads 202 retry row numbers from `output/qwen32b_8gpu/results/errors.csv`;
- projects `output/qwen32b_8gpu/results/annotations.csv` to the current three fields;
- rejects 20 prior successes whose relevant label conflicts with `training_grade=unusable` instead of automatically relabeling their content;
- reuses 3,283 compatible baseline rows and sends 222 rows to Qwen3-32B;
- writes isolated state under `output/qwen32b_8gpu_v4_legacy_error_repair/`;
- merges baseline and repaired rows into one review workbook and one combined `annotations.csv`;
- automatically creates `facebook_selected_commenters_for_crawl.csv` in the repair run's `results/` directory;
- retries only unresolved repair rows when the same command is run again.

The repair launcher uses four semantic retries by default because the input set consists entirely of prior failures. Override with `SEMANTIC_RETRIES` if needed.

## 6. Stop the services

```bash
scripts/stop_vllm_8gpu.sh
```

The stop script handles only live processes recorded in `run/vllm-8000.pid` and `run/vllm-8001.pid`. It does not scan for or terminate other vLLM jobs on the server.

## Override examples

After the 64-row sample is stable, you may test 24 workers. Keep the default 16 when long threads are common:

```bash
WORKERS=24 scripts/run_full_8gpu_vllm.sh
```

Override server memory settings for one invocation:

```bash
GPU_MEMORY_UTILIZATION=0.86 \
MAX_NUM_SEQS=12 \
MAX_MODEL_LEN=32768 \
scripts/start_vllm_8gpu_qwen32b.sh
```

Use remote endpoints:

```bash
BASE_URLS=http://10.0.0.10:8000/v1,http://10.0.0.10:8001/v1 \
scripts/run_full_8gpu_vllm.sh
```

If the API uses a different `--served-model-name`, set the same client model name:

```bash
API_MODEL_NAME=my-qwen32b scripts/run_full_8gpu_vllm.sh
```

## Offline dry run

The dry run performs input validation, writes the deterministic sample selection, and summarizes request lengths. It does not call the model or save a comment-text preview.

```bash
python annotate_facebook_threads.py \
  --input-xlsx input/facebook_comments_comprehensive_final.xlsx \
  --prompt-file input/ai_annotation_prompt.md \
  --schema-file config/annotation_schema.json \
  --output-dir output/dry_run_v4_three_fields \
  --output-xlsx output/dry_run_v4_three_fields/final/dry_run.xlsx \
  --model Qwen3-32B \
  --base-urls http://127.0.0.1:8000/v1,http://127.0.0.1:8001/v1 \
  --workers 16 \
  --timeout 180 \
  --network-retries 5 \
  --semantic-retries 2 \
  --max-output-tokens 768 \
  --max-thread-chars 40000 \
  --max-post-chars 8000 \
  --sample-size 64 \
  --seed 20260720 \
  --disable-thinking \
  --progress-every 10 \
  --dry-run
```
