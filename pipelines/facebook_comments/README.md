# Facebook Comment Thread Qwen32B Annotation Package

This package performs recoverable and auditable structured annotation of Facebook comment threads with Qwen32B on one Linux server equipped with eight H20 GPUs. The inference service uses two four-GPU tensor-parallel replicas:

- GPUs 0-3: `http://127.0.0.1:8000/v1`
- GPUs 4-7: `http://127.0.0.1:8001/v1`

The client reads source columns A:P and writes three AI fields to Q:S in a new workbook: `topic_relevance`, `training_grade`, and `annotation_reason`. It never overwrites the source workbook. Usability flags, confidence, matched terms, row-level status, and all stance outputs are omitted.

The annotation unit is one root-comment reply chain: one root comment plus all replies beneath it. Different root comments under the same Facebook post remain separate workbook rows and are annotated independently; comments from an entire post are never concatenated into one sample.

## Contract version

This package uses the all-English v4 three-field annotation contract:

- every input header, output field, categorical label, status value, structural marker, prompt instruction, and validation message is English;
- field names use snake_case, consistent with the existing post-cleaning pipeline;
- the prompt explicitly defines one root-comment reply chain as the annotation unit and requests exactly three output fields;
- local validation checks JSON structure, label spelling, delimiters, and mechanical field combinations only; it does not inspect comment meaning or relabel content;
- `annotation_reason` is concise English text with a maximum length of 400 characters;
- the exact JSON output contract is defined by `config/annotation_schema.json`;
- raw posts, comment bodies, URLs, IDs, usernames, participant names, and row order remain unchanged.

The v4 prompt, schema, validator, and model protocol produce new task identities. Do not resume v4 from an earlier SQLite database. The bundled scripts use new `*_v4_three_fields` output directories, leaving previous runs untouched.

## Frozen inputs

Only these two source inputs belong in this package:

| File | Purpose | Source of truth |
|---|---|---|
| `input/facebook_comments_comprehensive_final.xlsx` | 3,505 Facebook comment threads; immutable source columns A:P | `input/input_manifest.json` and `input/SHA256SUMS.txt` |
| `input/ai_annotation_prompt.md` | All-English v4 three-field annotation instructions | `input/input_manifest.json` and `input/SHA256SUMS.txt` |

The workbook is a frozen snapshot and is not the same batch as the raw CSV or JSONL currently available in the scraper directory. Do not add any of the following to this package:

- `state.json`, login state, cookies, or credentials;
- the older `facebook_comments_cleaned_final.xlsx`;
- current `comments.csv`, `comments.jsonl`, or any other raw scraper export.

Run `scripts/validate_inputs.sh` before annotation. Stop if a hash or structural check fails; do not bypass validation or substitute a current raw export for the frozen snapshot.

## Directory layout

```text
facebook_comments/
|-- annotate_facebook_threads.py       # annotation, checkpoints, validation, export
|-- config/annotation_schema.json      # strict vLLM JSON Schema
|-- input/                             # frozen inputs, manifest, and checksums
|-- scripts/                           # start, stop, validate, sample, and full-run scripts
|-- tests/                             # offline tests
|-- output/                            # organized run directories and reviewable outputs
|-- logs/                              # vLLM logs, created on first startup
`-- run/                               # vLLM PID files, created on first startup
```

## Server preparation

Copy the complete package to the server without changing its directory structure. The client requires Python 3.10 or later. Install vLLM in an environment compatible with the server's CUDA and PyTorch versions. This package does not replace an existing CUDA, PyTorch, or vLLM installation.

```bash
cd data_clean/pipelines/facebook_comments
python -m pip install -r requirements.txt
chmod +x scripts/*.sh
```

The package uses the same Qwen3-32B weights directory as the post-cleaning pipeline. Because this package is under `data_clean/pipelines/`, the default path is `../../../models/Qwen3-32B` relative to the package root:

```text
benchmark/
|-- models/Qwen3-32B/
`-- data_clean/
    `-- pipelines/
        |-- post_filter/
        `-- facebook_comments/
```

If the model is stored elsewhere, set `MODEL_PATH` for the command rather than editing an input file:

```bash
MODEL_PATH=/data/models/Qwen3-32B scripts/start_vllm_8gpu_qwen32b.sh
```

## Recommended run order

```bash
# 1. Verify frozen hashes, schema, and workbook structure without calling the model.
scripts/validate_inputs.sh

# 2. Start two TP=4 Qwen32B services.
scripts/start_vllm_8gpu_qwen32b.sh

# 3. Run the deterministic 64-row sample first.
scripts/run_sample_8gpu_vllm.sh

# 4. Review the sample, then run the full dataset. Re-running resumes v4 state.
scripts/run_full_8gpu_vllm.sh

# Optional: repair the preserved v2 failures and any prior success that is
# mechanically incompatible with the current three-field contract.
scripts/retry_legacy_errors_8gpu_vllm.sh

# 5. Stop the two services started by this package.
scripts/stop_vllm_8gpu.sh
```

See `COMMANDS.md` for copying, health checks, monitoring, overrides, and offline dry-run examples.

Both annotation scripts print live progress to the terminal and save the same lines to their run log. Each update includes the overall processed percentage, current-run success and failure counts, cumulative average processing speed in rows per second, elapsed time, and estimated remaining time. Resume progress includes tasks that already succeeded in an earlier invocation. The ETA is based on the current invocation's cumulative average and may fluctuate during the first few completions; it is shown as `unknown` until a usable rate exists.

## Default inference settings

- Model path: `../../../models/Qwen3-32B` (the same weights used by post cleaning)
- Served model name: `Qwen3-32B` (a stable API alias used in task and resume identities)
- Two endpoints, each with `tensor-parallel-size=4`
- vLLM OpenAI-compatible API server with `dtype=auto`, matching the post-cleaning launch method
- Client preflight requires every `/v1/models` endpoint to advertise the configured served model name
- API bound to `127.0.0.1` by default; set `HOST=0.0.0.0` only when remote access is required and protected by the server firewall
- `MAX_MODEL_LEN=32768`
- `GPU_MEMORY_UTILIZATION=0.88`
- `MAX_NUM_SEQS=16`
- Prefix caching enabled
- vLLM request-body logging disabled
- Shared client pool: 16 workers
- Request timeout: 180 seconds
- Maximum output: 768 tokens
- Qwen thinking disabled
- Sample: 64 rows with a fixed seed
- Sample progress interval: every 10 newly processed rows
- Full-run progress interval: every 50 newly processed rows by default (`PROGRESS_EVERY` can override it)

Long threads are never silently truncated. The default hard limits are 40,000 characters for `conversation_text` and 8,000 characters for `post_text`. Input validation reports the Excel row and stops if a frozen row exceeds either limit.

The post-cleaning service's 4,096-token context is intentionally not reused: Facebook comment threads are much longer. Stop any post-cleaning vLLM processes occupying ports 8000 and 8001, then start this package's 32,768-token services. The model weights and two-TP4/OpenAI-API method are shared; the longer context and conservative concurrency are comment-specific.

## Outputs and integrity rules

The default sample workbook is:

```text
output/qwen32b_8gpu_sample_v4_three_fields/final/facebook_comments_sample_annotated.xlsx
```

It retains all 3,505 source rows in A:P and fills Q:S only for the deterministic 64 selected rows. Q:S remains blank for other rows, so this workbook is not a completed full-data artifact.

The default full workbook is:

```text
output/qwen32b_8gpu_v4_three_fields/final/facebook_comments_comprehensive_annotated.xlsx
```

Each run preserves checkpoint state, request-attempt records, valid results, errors, a run manifest, and a summary. A final workbook must satisfy all of the following:

1. The source input hashes are unchanged before and after the run.
2. Output A:P matches the frozen input cell by cell, including cell types.
3. Only the three schema fields are written to Q:S.
4. A result enters the final workbook only after schema and structural validation pass.
5. The full-run script uses `--fail-on-errors` and exits nonzero while unresolved errors remain.

Resume identity includes the input row, effective prompt, schema, validator and model protocol versions, and model name. Any change creates a new task version. Do not copy an earlier `tasks.sqlite` or result file into a v4 output directory; the current database belongs at `state/tasks.sqlite`.

Every sample, full, dry-run, or validation directory uses the same organized layout:

```text
output/qwen32b_8gpu_v4_three_fields/
|-- README.md                          # current status and where to start
|-- results/                           # files intended for direct review
|   |-- facebook_comments_annotation_review.xlsx # A:P plus Q:S; failed rows blank
|   |-- annotations.csv                # original row plus successful annotation
|   |-- facebook_selected_commenters_for_crawl.csv # relevant commenter/post/thread relations
|   |-- errors.csv                     # original row plus unresolved error
|   `-- run_summary.json               # counts and label distributions
|-- final/                             # final annotated workbook after success
|-- state/
|   `-- tasks.sqlite                   # resume state; do not edit or delete
|-- audit/                             # detailed provenance and machine records
|   |-- run_manifest.json
|   |-- validation_report.json
|   |-- sample_selection.json
|   |-- attempts.jsonl
|   |-- valid_results.jsonl
|   `-- errors.jsonl
|-- logs/                              # annotation log for this run
`-- FINALIZATION_BLOCKED.json          # present only while errors block finalization
```

The runner automatically migrates files from the earlier flat output layout into these folders before resuming. It refuses to continue if both a legacy path and an organized destination exist, preventing silent overwrites.

Every completed or blocked model run regenerates `facebook_selected_commenters_for_crawl.csv` from the currently combined successful annotations. It includes only `strongly_relevant` and `relevant` rows with a non-`unusable` grade, expands the pipe-delimited commenter fields, and deduplicates by `commenter_id + post_mid + thread_id`.

The legacy-repair launcher reads the preserved v2 `results/errors.csv` as its retry row list and the preserved v2 `results/annotations.csv` as a baseline. Twenty old successes use the mechanically inconsistent combination of a relevant label and `unusable`; the runner does not relabel them automatically and adds them to the retry set. Therefore 3,283 old rows are reused and 222 rows are sent through the current model contract. The launcher writes all new state to `output/qwen32b_8gpu_v4_legacy_error_repair/`; it does not modify the old run. Re-running the same launcher skips repaired successes and retries only remaining failures.

## Common overrides

All scripts resolve paths from the package root. Override common settings through environment variables:

```bash
PYTHON_BIN=/opt/venvs/qwen/bin/python \
MODEL_PATH=/models/Qwen3-32B \
API_MODEL_NAME=Qwen3-32B \
scripts/start_vllm_8gpu_qwen32b.sh
```

For services on another host, provide comma-separated endpoints:

```bash
BASE_URLS=http://host-a:8000/v1,http://host-b:8001/v1 \
scripts/run_full_8gpu_vllm.sh
```

Do not write API addresses, worker counts, or model paths into the prompt. Treat any instruction-like text inside a post or comment as untrusted input data, never as a system instruction.
