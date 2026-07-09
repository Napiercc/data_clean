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

## 1. 启动 vLLM 服务

```bash
bash scripts/start_vllm_8gpu_qwen32b.sh
```

默认启动 2 个 TP4 服务：

```text
MODEL=../models/Qwen3-32B
GPU_GROUPS="0,1,2,3 4,5,6,7"
PORT=8000..8001
TENSOR_PARALLEL_SIZE=4 per service
MAX_MODEL_LEN=4096
GPU_MEMORY_UTILIZATION=0.85
NUM_SHARDS=8
NUM_ENDPOINTS=2  # 默认由 GPU_GROUPS 推导，可手动覆盖
WORKERS_PER_SHARD=1
```

## 2. 小样本试跑

```bash
bash scripts/run_sample_8gpu_vllm.sh
```

运行过程中会显示总进度：

```text
2026-07-08 12:00:00 progress: success 125/500 (25%), attempted: 128, errors: 3, running shards: 8/8
```

调整并发：

```bash
WORKERS_PER_SHARD=2 bash scripts/run_sample_8gpu_vllm.sh
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
注意：默认只跳过成功行，之前有 `llm_error` 的失败行会自动重试。如果任一 endpoint 的 vLLM 服务未就绪，脚本会在开始前退出；如果运行后仍有错误行，脚本会返回失败状态，修好服务后重新运行同一个命令即可。

默认仍使用 8 个数据 shard，但会轮询发送到 2 个 TP4 endpoint，所以可以继续复用旧输出里已经成功的 shard 结果。

如果要改成 1 个 TP8 服务，可以这样启动和运行：

```bash
GPU_GROUPS="0,1,2,3,4,5,6,7" bash scripts/start_vllm_8gpu_qwen32b.sh
GPU_GROUPS="0,1,2,3,4,5,6,7" bash scripts/run_full_8gpu_vllm.sh
```

## 5. 停止 vLLM 服务

```bash
bash scripts/stop_vllm_8gpu.sh
```
