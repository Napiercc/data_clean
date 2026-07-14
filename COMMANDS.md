# Qwen3-32B vLLM Commands

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
bash scripts/stop_vllm_8gpu.sh
bash scripts/start_vllm_8gpu_qwen32b.sh
```

从旧的单 TP4 配置切换时，先停止旧服务，再启动两个 endpoint，避免端口 `8000` 或 GPU `4-7` 被占用。

默认启动 2 个 TP4 服务，使用全部 8 张卡：

```text
MODEL=../models/Qwen3-32B
GPU_GROUPS="0,1,2,3 4,5,6,7"
PORTS=8000,8001
TENSOR_PARALLEL_SIZE=4 per service
MAX_MODEL_LEN=4096
GPU_MEMORY_UTILIZATION=0.85
MAX_NUM_SEQS=32
```

`MAX_NUM_SEQS=32` 为每个动态请求池副本预留并发序列。默认会加载两份 TP4 模型，每份使用 4 张卡；筛选程序会把请求均衡分发到两个服务，以提升批量吞吐。

## 2. 小样本试跑

```bash
bash scripts/run_sample_8gpu_vllm.sh
```

运行过程中会显示总进度：

```text
2026-07-08 12:00:00 progress: success 125/500 (25%), attempted: 128, errors: 3, worker pool: 24 across 2 endpoints
```

调整并发：

```bash
WORKERS=32 bash scripts/run_sample_8gpu_vllm.sh
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

运行脚本使用一个动态 worker 池而不是 8 个静态 shard。首次启动时会导入旧 `shard_*` 的结果，之后会从 `output/qwen32b_8gpu/dynamic/` 续跑。成功行会跳过，之前有 `llm_error` 的失败行会自动重试；所有任务共享同一个队列，因此不会因为部分 shard 提前结束而让吞吐逐步下降。

默认参数为 `WORKERS=24`、`TIMEOUT=90`、`MAX_RETRIES=2`、`MAX_POST_CHARS=2500`、`MAX_OUTPUT_TOKENS=1024`。Qwen3 thinking 默认开启；请求会按 8000、8001 两个服务的实时 in-flight 数均衡分配。若 vLLM 把 thinking 内容和最终回答放在同一字段，程序会在解析前剥离 `<think>...</think>` 段。所有运行结束后的最新结果都会发布到 `merged/`，包括残留错误清单；如需让残留错误返回非零状态，使用：

```bash
FAIL_ON_ERRORS=1 bash scripts/run_full_8gpu_vllm.sh
```

如遇服务排队或显存压力，降低并发：

```bash
WORKERS=16 bash scripts/run_full_8gpu_vllm.sh
```

如果要改成 1 个 TP8 服务，可以对启动和运行使用相同的 GPU 分组：

```bash
GPU_GROUPS="0,1,2,3,4,5,6,7" bash scripts/start_vllm_8gpu_qwen32b.sh
GPU_GROUPS="0,1,2,3,4,5,6,7" bash scripts/run_full_8gpu_vllm.sh
```

## 5. 停止 vLLM 服务

```bash
bash scripts/stop_vllm_8gpu.sh
```
