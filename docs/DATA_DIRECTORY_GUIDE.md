# data_clean 数据目录导航

这份导航只回答三件事：每个流程做什么、最终结果在哪里、哪些文件只是运行状态。

## 1. 总体结构

```text
data_clean/
├─ docs/
│  └─ DATA_DIRECTORY_GUIDE.md
└─ pipelines/
   ├─ post_filter/
   │  ├─ input/                    # 帖子筛选输入
   │  ├─ scripts/                  # 运行与报告脚本
   │  └─ output/                   # 帖子筛选状态和结果
   ├─ facebook_comments/
   │  ├─ input/                    # 冻结的 Facebook 评论工作簿和 prompt
   │  ├─ config/                   # 输出 schema
   │  ├─ scripts/                  # 校验、推理、修复、报告脚本
   │  ├─ tests/                    # 离线测试
   │  └─ output/                   # Facebook 各次运行结果
   └─ other_platform_comments/
      ├─ scripts/                  # 评论匹配与标准化脚本
      └─ output/                   # Instagram/YouTube 标准输入
```

## 2. 帖子筛选：`pipelines/post_filter`

作用：对规则筛选后的多平台帖子/视频进行 Qwen3-32B 二次筛选。

| 类型 | 路径 | 说明 |
|---|---|---|
| 输入 | `input/post_relevance_filtered.csv` | 规则筛选后的帖子候选集 |
| 当前最终数据 | `output/qwen32b_8gpu/merged/llm_post_relevance_filtered.csv` | LLM 最终保留的帖子/视频 |
| 人工查看报告 | `output/qwen32b_8gpu/merged/llm_summary_report.html` | 平台、topic、抽样和评论匹配报告 |
| 全部逐 topic 判断 | `output/qwen32b_8gpu/merged/llm_post_relevance_pairs.csv` | 一行表示一个 `post + topic` |
| 错误清单 | `output/qwen32b_8gpu/merged/llm_post_relevance_errors.csv` | 仅在有失败时需要处理 |
| 续跑状态 | `output/qwen32b_8gpu/dynamic/` | 正式运行断点，不是最终交付文件 |
| 旧分片状态 | `output/qwen32b_8gpu/shard_*` | 旧版运行记录，保留供追溯 |

运行入口：

```bash
cd data_clean/pipelines/post_filter
bash scripts/run_full_8gpu_vllm.sh
```

详细参数见 `pipelines/post_filter/COMMANDS.md`。

## 3. Facebook 评论：`pipelines/facebook_comments`

作用：把同一帖子下的每条根评论及其回复链作为一个独立样本，输出三列模型标注，并保留原始评论字段。

| 类型 | 路径 | 说明 |
|---|---|---|
| 冻结输入 | `input/facebook_comments_comprehensive_final.xlsx` | 原始评论工作簿，不覆盖 |
| Prompt | `input/ai_annotation_prompt.md` | 英文三字段标注规范 |
| 当前最终数据 | `output/qwen32b_8gpu_v4_legacy_error_repair/final/facebook_comments_comprehensive_annotated.xlsx` | 原字段加三列标注 |
| 人工审查报告 | `output/qwen32b_8gpu_v4_legacy_error_repair/results/facebook_comment_summary_report.html` | topic 序号、翻译样本和筛选逻辑 |
| 后续爬取名单 | `output/qwen32b_8gpu_v4_legacy_error_repair/results/facebook_selected_commenters_for_crawl.csv` | 保留评论者 ID 等追踪字段 |
| 机器结果 | `output/qwen32b_8gpu_v4_legacy_error_repair/results/annotations.csv` | 便于程序复用的逐行结果 |
| 旧运行 | `output/qwen32b_8gpu/` | 历史运行及旧错误记录，不是当前最终版本 |

运行入口：

```bash
cd data_clean/pipelines/facebook_comments
scripts/validate_inputs.sh
scripts/retry_legacy_errors_8gpu_vllm.sh
```

详细参数见 `pipelines/facebook_comments/COMMANDS.md`。

## 4. 其他平台评论：`pipelines/other_platform_comments`

作用：将旧 `socialmedia_data` 快照中的 Instagram、YouTube 评论匹配到已经保留的帖子/视频，并整理成后续模型可直接读取的格式。这里尚未执行评论相关性模型筛选。

| 类型 | 路径 | 说明 |
|---|---|---|
| 服务器输入 | `output/matched_comments_for_annotation.jsonl` | 流式读取、断点续跑最方便 |
| 表格输入 | `output/matched_comments_for_annotation.csv` | 程序复用与抽样核对 |
| 人工查看 | `output/matched_comments_for_annotation.xlsx` | 格式化工作簿 |
| 平台统计 | `output/comment_preparation_summary.csv` | 匹配数量和问题摘要 |
| 完整审计 | `output/comment_preparation_summary.json` | 来源路径、指纹和连接诊断 |

当前限制：Instagram 是部分历史采集快照；YouTube 只有顶层评论；匹配结果没有可用的 `parent_comment_id`；Reddit 原始评论正文为空；X 没有评论源文件。

运行入口：

```powershell
cd D:\social_network\benchmark\data_clean\pipelines\other_platform_comments
python scripts\prepare_other_platform_comments.py
```

## 5. 哪些文件可以忽略

- `__pycache__/`、`*.pyc`：Python 缓存，可自动重建。
- `logs/`、`*.log`：运行日志，排错时才看。
- `run/`：vLLM PID 状态，服务停止后无审查价值。
- `dynamic/`、`shard_*`、SQLite 状态文件：用于断点续跑和历史追溯，不是人工审查入口。

不要删除仍可能续跑的 `dynamic/`、Facebook 运行数据库或旧错误清单。历史 JSON 中出现旧绝对路径，表示当次运行发生时的位置，不影响移动后的脚本默认路径。
