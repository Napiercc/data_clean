$ErrorActionPreference = "Stop"

$Model = if ($env:MODEL) { $env:MODEL } else { "Qwen/Qwen3-32B-Instruct" }
$BaseUrl = if ($env:BASE_URL) { $env:BASE_URL } else { "http://localhost:8000/v1" }
$InputPath = if ($env:INPUT) { $env:INPUT } else { "input/post_relevance_filtered.csv" }
$OutputDir = if ($env:OUTPUT_DIR) { $env:OUTPUT_DIR } else { "output/qwen32b_full" }
$Workers = if ($env:WORKERS) { $env:WORKERS } else { "4" }

python llm_post_filter.py `
  --base_url $BaseUrl `
  --model $Model `
  --input $InputPath `
  --output_dir $OutputDir `
  --workers $Workers `
  --resume
