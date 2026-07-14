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
bash scripts/start_vllm_8gpu_qwen32b.sh
```

默认启动 1 个 TP4 服务，使用最后 4 张卡：

```text
MODEL=../models/Qwen3-32B
GPU_GROUPS="4,5,6,7"
PORT=8000
TENSOR_PARALLEL_SIZE=4
MAX_MODEL_LEN=4096
GPU_MEMORY_UTILIZATION=0.85
MAX_NUM_SEQS=32
```

`MAX_NUM_SEQS=32` 为动态请求池预留并发序列。默认服务仍是一个 TP4 模型，4 张卡共同承载同一模型，不会复制出 4 个模型实例。

## 2. 小样本试跑

```bash
bash scripts/run_sample_8gpu_vllm.sh
```

运行过程中会显示总进度：

```text
2026-07-08 12:00:00 progress: success 125/500 (25%), attempted: 128, errors: 3, worker pool: 16
```

调整并发：

```bash
WORKERS=24 bash scripts/run_sample_8gpu_vllm.sh
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

默认参数为 `WORKERS=16`、`TIMEOUT=90`、`MAX_RETRIES=2`、`MAX_POST_CHARS=2500`、`MAX_OUTPUT_TOKENS=1024`。Qwen3 thinking 默认开启；若 vLLM 把 thinking 内容和最终回答放在同一字段，程序会在解析前剥离 `<think>...</think>` 段。所有运行结束后的最新结果都会发布到 `merged/`，包括残留错误清单；如需让残留错误返回非零状态，使用：

```bash
FAIL_ON_ERRORS=1 bash scripts/run_full_8gpu_vllm.sh
```

如遇服务排队或显存压力，降低并发：

```bash
WORKERS=12 bash scripts/run_full_8gpu_vllm.sh
```

如果要改成 1 个 TP8 服务，可以这样启动和运行：

```bash
GPU_GROUPS="0,1,2,3,4,5,6,7" bash scripts/start_vllm_8gpu_qwen32b.sh
bash scripts/run_full_8gpu_vllm.sh
```

## 5. 停止 vLLM 服务

```bash
bash scripts/stop_vllm_8gpu.sh
```
