# Social Media Data Cleaning

本仓库按处理流程分为三个独立目录。脚本、输入、运行状态和输出均保存在对应流程内部，避免不同平台文件混放。

```text
data_clean/
├─ README.md
├─ docs/
│  └─ DATA_DIRECTORY_GUIDE.md
└─ pipelines/
   ├─ post_filter/                 # 多平台帖子/视频相关性筛选
   ├─ facebook_comments/           # Facebook 评论相关性与训练价值标注
   └─ other_platform_comments/     # Instagram/YouTube 评论匹配与标准化
```

## 直接查看

| 目的 | 位置 |
|---|---|
| 最终保留的帖子/视频 | `pipelines/post_filter/output/qwen32b_8gpu/merged/llm_post_relevance_filtered.csv` |
| 帖子筛选报告 | `pipelines/post_filter/output/qwen32b_8gpu/merged/llm_summary_report.html` |
| Facebook 评论最终工作簿 | `pipelines/facebook_comments/output/qwen32b_8gpu_v4_legacy_error_repair/final/facebook_comments_comprehensive_annotated.xlsx` |
| Facebook 评论审查报告 | `pipelines/facebook_comments/output/qwen32b_8gpu_v4_legacy_error_repair/results/facebook_comment_summary_report.html` |
| Facebook 评论者后续爬取名单 | `pipelines/facebook_comments/output/qwen32b_8gpu_v4_legacy_error_repair/results/facebook_selected_commenters_for_crawl.csv` |
| Instagram/YouTube 待标注评论 | `pipelines/other_platform_comments/output/matched_comments_for_annotation.csv` |

完整目录说明、运行入口和中间文件用途见 [数据目录导航](docs/DATA_DIRECTORY_GUIDE.md)。各流程的具体运行方法见其目录内的 `README.md` 和 `COMMANDS.md`。

## 运行约定

- 必须先进入对应的流程目录，再运行该目录下的脚本。
- 三个流程都只读取外部原始数据，不覆盖原始文件。
- `output/` 中的历史运行目录保留用于审计和断点续跑；不要仅凭目录名判断最新结果，应按导航中标注的“当前结果”路径查看。
- 服务器模型默认位于 `benchmark/models/Qwen3-32B`。帖子和 Facebook 评论流程的相对模型路径已按新结构更新。
