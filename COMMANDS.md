# 8GPU vLLM Commands

所有命令都在项目根目录执行。

## 1. 启动 8 个 vLLM 服务

```bash
bash scripts/start_vllm_8gpu_qwen32b.sh
```

默认会使用：

```text
MODEL=Qwen/Qwen3-32B-Instruct
GPU=0..7
PORT=8000..8007
MAX_MODEL_LEN=4096
```

如果要调参数：

```bash
MODEL=Qwen/Qwen3-32B-Instruct MAX_MODEL_LEN=4096 GPU_MEMORY_UTILIZATION=0.90 bash scripts/start_vllm_8gpu_qwen32b.sh
```

## 2. 小样本试跑

```bash
bash scripts/run_sample_8gpu_vllm.sh
```

调小或调大并发：

```bash
WORKERS_PER_SHARD=2 bash scripts/run_sample_8gpu_vllm.sh
WORKERS_PER_SHARD=8 bash scripts/run_sample_8gpu_vllm.sh
```

## 3. 正式全量运行

```bash
bash scripts/run_full_8gpu_vllm.sh
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

## 6. 单 GPU 调试

只在调试时使用：

```bash
bash scripts/start_vllm_qwen32b.sh
bash scripts/run_sample_vllm.sh
```
