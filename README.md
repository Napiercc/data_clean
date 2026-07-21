# Server LLM Post Filter

这是 posts-only 社交媒体数据的第二阶段 LLM 筛选项目。当前版本默认把 8 张卡拆成两套 TP4 vLLM 副本：GPU 0-3 使用端口 8000，GPU 4-7 使用端口 8001；默认模型路径是 `../models/Qwen3-32B`。

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

- `llm_post_filter.py`：唯一 Python 主程序，负责动态并发推理、失败重试、结果续跑与汇总。
- `input/post_relevance_filtered.csv`：规则筛选后的 posts-only 输入数据。
- `scripts/start_vllm_8gpu_qwen32b.sh`：默认启动 2 个 TP4 vLLM 服务，GPU `0-3` 使用端口 `8000`，GPU `4-7` 使用端口 `8001`。
- `scripts/run_sample_8gpu_vllm.sh`：小样本动态并发试跑。
- `scripts/run_full_8gpu_vllm.sh`：正式全量动态并发运行。
- `scripts/stop_vllm_8gpu.sh`：停止已启动的 vLLM 服务。
- `COMMANDS.md`：可直接复制使用的命令。

## 运行方式

进入项目目录：

```bash
cd data_clean
```

启动两套 TP4 vLLM 服务：

```bash
bash scripts/stop_vllm_8gpu.sh
bash scripts/start_vllm_8gpu_qwen32b.sh
```

第一次从旧的单 TP4 配置切换到当前双 TP4 配置时，必须先停止旧服务，避免端口 `8000` 或 GPU `4-7` 被占用。

两份 32B 权重默认按顺序加载：8000 就绪后才会开始加载 8001，以降低主机内存峰值。若确认服务器 RAM 充足，可用 `START_SEQUENTIALLY=0` 改回并行加载。

启动脚本会先对每个 GPU 组运行一次轻量 CUDA 预检。若出现 `error 802: system not yet initialized`，这是驱动/Fabric 初始化问题，不是模型或筛选代码问题；先修复服务器 GPU 状态再启动。

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
2026-07-08 12:00:00 progress: success 1250/29019 (4%), attempted: 1260, errors: 10, worker pool: 24 across 2 endpoints
```

默认每 15 秒刷新一次，可以用 `PROGRESS_INTERVAL` 调整：

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

所有运行脚本都启用了 `--resume`，中断后可以直接重新运行对应命令续跑。正式运行首次会导入旧 `shard_*` 目录的结果，之后以 `output/qwen32b_8gpu/dynamic/` 作为续跑状态；成功行会跳过，`llm_error` 行会重试。

运行脚本会在开始前检查默认 endpoints `8000` 和 `8001` 的 `/v1/models` 是否可用。如果任一 vLLM 服务未就绪，脚本会直接退出，不会把整批请求写成失败结果。即使仍有错误行，脚本也会先把最新结果发布到 `merged/`；下一次运行会继续重试。若需要把残留错误当作命令失败，可设置 `FAIL_ON_ERRORS=1`。

默认运行一个 24 路请求的共享 worker 池，持续从同一任务队列领取工作，并按两个 vLLM 服务当前 in-flight 请求数自动均衡。这样两套 TP4 模型可同时推理，先完成的任务也不会造成剩余任务近乎停滞。Qwen3 的 thinking 模式默认开启；单条帖子截断为 2500 字符，输出上限为 1024 tokens。若服务器把 thinking 内容内联在响应中，程序会在解析最终 JSON 前剥离 `<think>...</think>` 段。

可按显存和吞吐调整并发，例如：

```bash
WORKERS=32 bash scripts/run_full_8gpu_vllm.sh
```

如果出现明显的排队超时或显存压力，先降到 `WORKERS=16`。每个 vLLM 副本默认允许 32 个并发序列，可用 `MAX_NUM_SEQS` 在启动服务时调整。
