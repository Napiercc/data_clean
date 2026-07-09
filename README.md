# Server LLM Post Filter

这是 posts-only 社交媒体数据的第二阶段 LLM 筛选项目。当前版本默认使用 8GPU vLLM tensor-parallel 分组运行方式，默认模型路径是 `../models/Qwen3-32B`。

服务器目录结构应为：

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

- `llm_post_filter.py`：唯一 Python 主程序，负责 shard 推理、并发请求、结果汇总、8 shard 合并。
- `input/post_relevance_filtered.csv`：规则筛选后的 posts-only 输入数据。
- `scripts/start_vllm_8gpu_qwen32b.sh`：默认启动 2 个 TP4 vLLM 服务，GPU `0,1,2,3` 使用端口 `8000`，GPU `4,5,6,7` 使用端口 `8001`。
- `scripts/run_sample_8gpu_vllm.sh`：8GPU 小样本试跑。
- `scripts/run_full_8gpu_vllm.sh`：8GPU 正式全量运行。
- `scripts/stop_vllm_8gpu.sh`：停止 8 个 vLLM 服务。
- `COMMANDS.md`：可直接复制使用的命令。

## 运行方式

进入项目目录：

```bash
cd data_clean
```

启动 2 个 TP4 vLLM 服务：

```bash
bash scripts/start_vllm_8gpu_qwen32b.sh
```

另开一个终端，先小样本试跑：

```bash
bash scripts/run_sample_8gpu_vllm.sh
```

正式全量运行：

```bash
bash scripts/run_full_8gpu_vllm.sh
```

运行过程中主终端会定时显示总进度：

```text
2026-07-08 12:00:00 progress: success 1250/29019 (4%), attempted: 1260, errors: 10, running shards: 8/8
```

默认每 30 秒刷新一次，可以用 `PROGRESS_INTERVAL` 调整：

```bash
PROGRESS_INTERVAL=10 bash scripts/run_full_8gpu_vllm.sh
```

停止 vLLM 服务：

```bash
bash scripts/stop_vllm_8gpu.sh
```

## 输出文件

最终合并结果会写到：

```text
output/qwen32b_8gpu/merged/
```

核心结果文件：

```text
output/qwen32b_8gpu/merged/llm_post_relevance_filtered.csv
```

合并目录包括：

- `llm_post_relevance_pairs.jsonl`
- `llm_post_relevance_pairs.csv`
- `llm_post_relevance_filtered.csv`
- `llm_post_relevance_removed.csv`
- `llm_post_relevance_review.csv`
- `llm_post_relevance_errors.csv`
- `llm_platform_summary.csv`
- `llm_topic_summary.csv`
- `llm_run_summary.json`
- `llm_merge_summary.json`

`llm_run_summary.json` 会写出 `success_rows`、`error_rows`、`error_rate` 和 `error_counts`。

所有运行脚本都启用了 `--resume`，中断后可以直接重新运行对应命令续跑。默认只跳过已经成功的行；之前写入了 `llm_error` 的失败行会在下一次运行时自动重试。

运行脚本会在开始前检查默认 endpoint `8000-8001` 的 `/v1/models` 是否可用。如果任一 vLLM 服务未就绪，脚本会直接退出，不会把整批请求写成失败结果。正式脚本和小样本脚本也启用了失败保护：如果运行结束后仍存在 `llm_error`，脚本会返回非零退出码，修复服务后重新运行同一命令即可重试失败行。

默认仍保留 `NUM_SHARDS=8`，但会把 8 个数据 shard 轮询发送到 2 个 TP4 endpoint。这样可以复用旧输出中已经成功的 shard 结果，只重试失败行。
