# 8GPU vLLM Commands

所有命令都在项目根目录 `data_clean/` 下执行。默认模型路径是 `../models/Qwen3-32B`。

要求目录结构：

```text
workspace/
├── data_clean/
└── models/
    └── Qwen3-32B/
        ├── config.json
        ├── tokenizer.json
        ├── model-*.safetensors
        └── ...
```

## 1. 启动 8 个 vLLM 服务

```bash
bash scripts/start_vllm_8gpu_qwen32b.sh
```

默认配置：

```text
MODEL=../models/Qwen3-32B
GPU=0..7
PORT=8000..8007
MAX_MODEL_LEN=4096
GPU_MEMORY_UTILIZATION=0.90
```

## 2. 小样本试跑

```bash
bash scripts/run_sample_8gpu_vllm.sh
```

运行过程中会显示总进度：

```text
2026-07-08 12:00:00 progress: 125/500 (25%), running shards: 8/8
```

调整并发：

```bash
WORKERS_PER_SHARD=2 bash scripts/run_sample_8gpu_vllm.sh
WORKERS_PER_SHARD=8 bash scripts/run_sample_8gpu_vllm.sh
```

调整进度刷新间隔，单位是秒：

```bash
PROGRESS_INTERVAL=10 bash scripts/run_sample_8gpu_vllm.sh
```

## 3. 正式全量运行

```bash
bash scripts/run_full_8gpu_vllm.sh
```

如果要更频繁显示进度：

```bash
PROGRESS_INTERVAL=10 bash scripts/run_full_8gpu_vllm.sh
```

最终结果：

```text
output/qwen32b_8gpu/merged/llm_post_relevance_filtered.csv
```

## 4. 中断后续跑

直接重新运行同一个命令即可：

```bash
bash scripts/run_full_8gpu_vllm.sh
```

每个 shard 都启用了 `--resume`，已经写入 JSONL 的行会跳过。

## 5. 停止 vLLM 服务

```bash
bash scripts/stop_vllm_8gpu.sh
```
