# Server LLM Post Filter

这是 posts-only 社交媒体数据的第二阶段 LLM 筛选项目。当前版本面向正式服务器运行，默认使用 `Qwen/Qwen3-32B-Instruct`，并支持 8 张 H20 并行推理。

筛选目标：

1. 判断帖子是否真正贴近指定 topic，而不是只命中关键词。
2. 判断帖子是否带有立场。
3. 判断帖子是否有中高讨论潜力。

最终保留规则：

```text
topic_relevance in strongly_relevant / relevant
AND has_stance = true
AND discussion_potential in high / medium
```

## 文件说明

- `llm_post_filter.py`：唯一的 Python 主程序，负责单 shard 推理、并发请求、结果汇总、8 shard 合并。
- `input/post_relevance_filtered.csv`：规则筛选后的 posts-only 输入数据。
- `scripts/start_vllm_8gpu_qwen32b.sh`：启动 8 个 vLLM 服务，每张 GPU 一个服务，端口默认 `8000-8007`。
- `scripts/run_sample_8gpu_vllm.sh`：8GPU 小样本试跑。
- `scripts/run_full_8gpu_vllm.sh`：8GPU 正式全量运行。
- `scripts/stop_vllm_8gpu.sh`：停止 8 个 vLLM 服务。
- `scripts/start_vllm_qwen32b.sh`、`scripts/run_full_vllm.sh`、`scripts/run_sample_vllm.sh`：单 GPU 调试入口。
- `COMMANDS.md`：可直接复制使用的命令。

## 正式 8GPU 运行

在服务器项目根目录执行：

```bash
bash scripts/start_vllm_8gpu_qwen32b.sh
```

确认 8 个 vLLM 服务 ready 后，建议先小样本试跑：

```bash
bash scripts/run_sample_8gpu_vllm.sh
```

正式全量运行：

```bash
bash scripts/run_full_8gpu_vllm.sh
```

最终合并结果会写到：

```text
output/qwen32b_8gpu/merged/
```

核心结果文件：

```text
output/qwen32b_8gpu/merged/llm_post_relevance_filtered.csv
```

停止 vLLM 服务：

```bash
bash scripts/stop_vllm_8gpu.sh
```

## 并行方式

8GPU 版本会做两层并行：

1. 将输入数据确定性切成 8 个 shard。
2. 每个 shard 连接一个独立的 vLLM 端口，并用 `WORKERS_PER_SHARD` 发起并发请求。

默认配置：

```text
MODEL=Qwen/Qwen3-32B-Instruct
NUM_GPUS=8
NUM_SHARDS=8
BASE_PORT=8000
WORKERS_PER_SHARD=4
MAX_MODEL_LEN=4096
GPU_MEMORY_UTILIZATION=0.90
```

如果显存或吞吐不稳定，先把 `WORKERS_PER_SHARD` 调低到 2；如果 GPU 利用率不高，再调到 6 或 8。

## 输出文件

每个 shard 会生成独立目录：

```text
output/qwen32b_8gpu/shard_0/
...
output/qwen32b_8gpu/shard_7/
```

合并后的正式结果在：

```text
output/qwen32b_8gpu/merged/
```

其中包括：

- `llm_post_relevance_pairs.jsonl`
- `llm_post_relevance_pairs.csv`
- `llm_post_relevance_filtered.csv`
- `llm_post_relevance_removed.csv`
- `llm_post_relevance_review.csv`
- `llm_platform_summary.csv`
- `llm_topic_summary.csv`
- `llm_run_summary.json`
- `llm_merge_summary.json`

所有运行脚本都启用了 `--resume`，中断后可以直接重新运行对应命令续跑。
