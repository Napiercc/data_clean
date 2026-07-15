#!/usr/bin/env python3
"""Build a self-contained Chinese HTML summary for merged LLM filter results."""

from __future__ import annotations

import argparse
import csv
import html
import json
import random
import sys
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from llm_post_filter import (  # noqa: E402
    DEFAULT_MAX_OUTPUT_TOKENS,
    DEFAULT_MAX_POST_CHARS,
    KEEP_DISCUSSION,
    KEEP_TOPIC_RELEVANCE,
    SYSTEM_PROMPT,
)


DEFAULT_PAIRS = ROOT / "output/qwen32b_8gpu/merged/llm_post_relevance_pairs.csv"
DEFAULT_REPORT = ROOT / "output/qwen32b_8gpu/merged/llm_summary_report.html"
DEFAULT_TRANSLATIONS = ROOT / "output/qwen32b_8gpu/merged/llm_sample_translations.json"
AUDIT_SEED = 20260714
FOCUS_SEED = 20260715


SYSTEM_PROMPT_CN = """你是社交媒体研究数据集的严格第二阶段审查员。

目标：
只保留真正贴近指定议题、表达或暗含立场、并且可能引发公共讨论的帖子。

规则：
- 根据指定议题判断帖子，而不是只根据关键词判断。
- 不要因为帖子提到了关键词就保留。
- 帖子必须讨论该议题、政策问题、公共辩论、直接相关事件，或直接相关的个人经历。
- 事实性标题或中性新闻摘要可以与议题相关，但除非它表达支持、批评、评价、担忧、要求、嘲讽或其他立场，否则不算有立场。
- 立场指针对该议题、政策或辩论的立场、评价、偏好、担忧、支持、反对、批评、要求，或暗含态度。
- 不要把一般情绪当作立场，除非这种情绪明确指向指定议题。
- 讨论潜力指文本本身是否可能引发分歧、回复、辩论、赞同/反对或公共反应。
- 不要使用点赞、回复、浏览量、转发量或作者影响力来判断讨论潜力。
- 如果文本像广告、垃圾信息、泛娱乐内容、招聘帖或无关个人动态，不要保留。
- 如果文本被截断或过于含糊、无法判断，应酌情标记为 insufficient_context 或 unclear。
- 仅当相关性为 strongly_relevant 或 relevant、has_stance 为 true，且讨论潜力为 high 或 medium 时，final_keep 才能为 true。
- 每个 reason 字段最多 16 个英文词，不使用引号或换行。"""

USER_PROMPT = """Review this social media post for the assigned topic.
Return only the structured JSON object requested by the schema.

{
  "platform": "<platform>",
  "record_type": "<record_type>",
  "topic": "<assigned topic>",
  "retrieval_keyword": "<keyword that matched the post>",
  "rule_label": "<first-stage rule label>",
  "rule_reason": "<first-stage rule reason>",
  "post_text": "<post text, truncated to 2500 characters if needed>"
}"""

USER_PROMPT_CN = """请根据指定议题审查这条社交媒体帖子。
只返回 schema 要求的结构化 JSON 对象。

{
  "platform": "<平台>",
  "record_type": "<记录类型>",
  "topic": "<指定议题>",
  "retrieval_keyword": "<命中该帖子的检索关键词>",
  "rule_label": "<第一阶段规则标签>",
  "rule_reason": "<第一阶段规则原因>",
  "post_text": "<帖子正文；如有需要，截断至 2500 个字符>"
}"""


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Generate the LLM filtering HTML report.")
    parser.add_argument("--pairs", type=Path, default=DEFAULT_PAIRS, help="Merged pairs CSV file.")
    parser.add_argument("--output", type=Path, default=DEFAULT_REPORT, help="HTML report path.")
    parser.add_argument(
        "--translations",
        type=Path,
        default=DEFAULT_TRANSLATIONS,
        help="JSON mapping of llm_row_key to Chinese sample translation.",
    )
    parser.add_argument("--audit-samples", type=int, default=12, help="Number of randomly retained audit samples.")
    parser.add_argument("--focus-samples", type=int, default=8, help="Number of retained neutral/unclear focus samples.")
    parser.add_argument(
        "--finalized",
        action="store_true",
        help="Label failed rows as intentionally excluded in the final report instead of recommending a retry.",
    )
    return parser.parse_args()


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8-sig", newline="") as file:
        return list(csv.DictReader(file))


def dedupe_rows(rows: list[dict[str, str]]) -> list[dict[str, str]]:
    latest: dict[str, dict[str, str]] = {}
    order: list[str] = []
    for row in rows:
        key = row.get("llm_row_key") or row.get("record_id", "")
        if key not in latest:
            order.append(key)
        latest[key] = row
    return [latest[key] for key in order]


def is_true(value: str) -> bool:
    return str(value).strip().lower() == "true"


def is_success(row: dict[str, str]) -> bool:
    return not str(row.get("llm_error", "")).strip()


def is_kept(row: dict[str, str]) -> bool:
    return is_true(row.get("llm_final_keep", ""))


def fmt_number(value: int) -> str:
    return f"{value:,}"


def pct(numerator: int, denominator: int) -> str:
    return f"{(100 * numerator / denominator):.1f}%" if denominator else "-"


def pct_value(numerator: int, denominator: int) -> float:
    return 100 * numerator / denominator if denominator else 0.0


def esc(value: Any) -> str:
    return html.escape(str(value or ""), quote=True)


def text_excerpt(text: str, limit: int = 440) -> str:
    clean = " ".join(str(text or "").replace("_", " ").split())
    return clean if len(clean) <= limit else clean[: limit - 1].rstrip() + "…"


def normalized_error(error: str) -> str:
    error = str(error or "").strip()
    if error.startswith("JSONDecodeError"):
        return "JSON 输出解析失败"
    if "Connection refused" in error:
        return "连接被拒绝"
    if "timed out" in error.lower() or "timeout" in error.lower():
        return "请求超时"
    if error.startswith("HTTP "):
        return error.split(":", 1)[0]
    return error[:90] or "未知错误"


def tag(value: str, kind: str = "") -> str:
    css_class = f"tag {kind}".strip()
    return f'<span class="{css_class}">{esc(value)}</span>'


def table(headers: list[str], rows: list[list[str]], numeric_columns: set[int] | None = None) -> str:
    numeric_columns = numeric_columns or set()
    head = "".join(
        f'<th class="{"num" if index in numeric_columns else ""}">{esc(header)}</th>'
        for index, header in enumerate(headers)
    )
    body_rows = []
    for row in rows:
        cells = "".join(
            f'<td class="{"num" if index in numeric_columns else ""}">{cell}</td>'
            for index, cell in enumerate(row)
        )
        body_rows.append(f"<tr>{cells}</tr>")
    return f"<table><thead><tr>{head}</tr></thead><tbody>{''.join(body_rows)}</tbody></table>"


def metric_card(label: str, value: str, note: str, color: str, width: float | None = None) -> str:
    bar = ""
    if width is not None:
        bar = f'<div class="bar"><span class="{color}" style="width:{min(max(width, 0), 100):.2f}%"></span></div>'
    return (
        '<div class="card">'
        f'<div class="metric-label">{esc(label)}</div><div class="metric-value">{esc(value)}</div>'
        f'<div class="metric-note">{esc(note)}</div>{bar}</div>'
    )


def sample_card(row: dict[str, str], index: int, translations: dict[str, str]) -> str:
    key = row.get("llm_row_key", "")
    translation = translations[key]
    meta = (
        f"#{index} / {row.get('platform', '')} / topic: {row.get('topic', '')} / "
        f"relevance: {row.get('llm_topic_relevance', '')} / "
        f"stance: {row.get('llm_stance_label', '')} / "
        f"discussion: {row.get('llm_discussion_potential', '')} / "
        f"confidence: {row.get('llm_confidence', '')}"
    )
    link = ""
    if row.get("url", "").startswith(("https://", "http://")):
        link = (
            '<div class="sample-link"><a href="'
            + esc(row["url"])
            + '" target="_blank" rel="noopener noreferrer">打开原始链接</a></div>'
        )
    return (
        '<article class="sample">'
        f'<div class="sample-meta">{esc(meta)}</div>'
        f'<div class="sample-text"><strong>原文：</strong>{esc(text_excerpt(row.get("cont_clean", "")))}</div>'
        f'<div class="sample-text"><strong>中文译文：</strong>{esc(translation)}</div>'
        f'<div class="sample-text"><strong>LLM 原因：</strong>{esc(row.get("llm_final_keep_reason", ""))}</div>'
        f"{link}</article>"
    )


def group_rows(rows: list[dict[str, str]], field: str) -> list[dict[str, Any]]:
    grouped: dict[str, list[dict[str, str]]] = defaultdict(list)
    for row in rows:
        grouped[row.get(field, "未标注") or "未标注"].append(row)
    result: list[dict[str, Any]] = []
    for name, values in grouped.items():
        total = len(values)
        success = sum(is_success(row) for row in values)
        kept = sum(is_kept(row) for row in values)
        result.append({"name": name, "total": total, "success": success, "failed": total - success, "kept": kept})
    return result


def kept_neutral_count(rows: list[dict[str, str]]) -> int:
    return sum(
        is_kept(row) and row.get("llm_stance_label") in {"neutral_or_factual", "unclear"}
        for row in rows
    )


def render_report(
    rows: list[dict[str, str]],
    pairs_path: Path,
    translations: dict[str, str],
    audit_count: int,
    focus_count: int,
    finalized: bool,
) -> str:
    total = len(rows)
    success_rows = [row for row in rows if is_success(row)]
    failed_rows = [row for row in rows if not is_success(row)]
    kept_rows = [row for row in rows if is_kept(row)]
    review_rows = [
        row for row in rows if is_true(row.get("llm_needs_human_review", "")) or not is_success(row)
    ]
    success = len(success_rows)
    failed = len(failed_rows)
    kept = len(kept_rows)
    neutral_kept = kept_neutral_count(rows)

    if not kept_rows:
        raise SystemExit("No retained rows found; cannot build audit samples.")
    audit_count = min(audit_count, len(kept_rows))
    focus_candidates = [
        row for row in kept_rows if row.get("llm_stance_label") in {"neutral_or_factual", "unclear"}
    ]
    focus_count = min(focus_count, len(focus_candidates))
    audit_rows = random.Random(AUDIT_SEED).sample(kept_rows, audit_count)
    focus_rows = random.Random(FOCUS_SEED).sample(focus_candidates, focus_count)
    required_keys = {row.get("llm_row_key", "") for row in audit_rows + focus_rows}
    missing_translations = sorted(key for key in required_keys if not translations.get(key, "").strip())
    if missing_translations:
        raise SystemExit(
            "Missing Chinese translations for sampled rows: " + ", ".join(missing_translations)
        )

    errors = Counter(normalized_error(row.get("llm_error", "")) for row in failed_rows)
    relevance = Counter(row.get("llm_topic_relevance", "") or "无结果" for row in rows)
    stance = Counter(row.get("llm_stance_label", "") or "无结果" for row in rows)
    discussion = Counter(row.get("llm_discussion_potential", "") or "无结果" for row in rows)
    models = Counter(row.get("llm_model", "") or "未记录" for row in success_rows)
    model_text = ", ".join(f"{name} ({count:,})" for name, count in models.most_common())

    platform_data = sorted(group_rows(rows, "platform"), key=lambda item: item["total"], reverse=True)
    platform_table = table(
        ["平台", "总数", "成功", "失败", "保留", "错误率", "成功口径保留率"],
        [
            [
                esc(item["name"]),
                fmt_number(item["total"]),
                fmt_number(item["success"]),
                fmt_number(item["failed"]),
                fmt_number(item["kept"]),
                pct(item["failed"], item["total"]),
                pct(item["kept"], item["success"]),
            ]
            for item in platform_data
        ],
        {1, 2, 3, 4},
    )

    topic_data = group_rows(rows, "topic")
    top_topics = sorted(topic_data, key=lambda item: item["kept"], reverse=True)[:12]
    eligible_topics = [item for item in topic_data if item["success"] >= 20]
    high_topics = sorted(
        eligible_topics,
        key=lambda item: pct_value(item["kept"], item["success"]),
        reverse=True,
    )[:8]
    low_topics = sorted(eligible_topics, key=lambda item: pct_value(item["kept"], item["success"]))[:8]

    def topic_table(items: list[dict[str, Any]], include_total: bool) -> str:
        headers = ["Topic", "总数", "成功", "失败", "保留", "成功口径保留率"] if include_total else [
            "Topic",
            "成功",
            "保留",
            "保留率",
        ]
        values = []
        for item in items:
            if include_total:
                values.append(
                    [
                        f'<span class="topic">{esc(item["name"])}</span>',
                        fmt_number(item["total"]),
                        fmt_number(item["success"]),
                        fmt_number(item["failed"]),
                        fmt_number(item["kept"]),
                        pct(item["kept"], item["success"]),
                    ]
                )
            else:
                values.append(
                    [
                        f'<span class="topic">{esc(item["name"])}</span>',
                        fmt_number(item["success"]),
                        fmt_number(item["kept"]),
                        pct(item["kept"], item["success"]),
                    ]
                )
        return table(headers, values, {1, 2, 3, 4} if include_total else {1, 2})

    removal_reasons: Counter[str] = Counter()
    for row in success_rows:
        if is_kept(row):
            continue
        failed_checks: list[str] = []
        if row.get("llm_topic_relevance") not in KEEP_TOPIC_RELEVANCE:
            failed_checks.append("相关性不足")
        if not is_true(row.get("llm_has_stance", "")):
            failed_checks.append("无立场")
        if row.get("llm_discussion_potential") not in KEEP_DISCUSSION:
            failed_checks.append("讨论潜力低/不明确")
        removal_reasons[" + ".join(failed_checks) or "模型判定不一致"] += 1

    label_rows = [
        ["相关性", esc(name), fmt_number(count), pct(count, total)] for name, count in relevance.most_common()
    ]
    label_rows += [["立场标签", esc(name), fmt_number(count), pct(count, total)] for name, count in stance.most_common()]
    label_rows += [
        ["讨论潜力", esc(name), fmt_number(count), pct(count, total)] for name, count in discussion.most_common()
    ]

    error_block = (
        table(
            ["错误类型", "数量", "占失败比例"],
            [[esc(name), fmt_number(count), pct(count, failed)] for name, count in errors.most_common()],
            {1},
        )
        if failed
        else '<div class="empty-state">没有 LLM 调用错误。</div>'
    )

    if finalized:
        conclusion = (
            f"本报告为最终版：{fmt_number(success)} 条成功推理结果已纳入统计；"
            f"{fmt_number(failed)} 条模型输出格式失败记录已排除，不作为模型筛选结论解释。"
        )
    elif failed:
        conclusion = (
            f"当前结果接近完整：仅 {fmt_number(failed)} 条记录调用失败，占全量 {pct(failed, total)}。"
            f"建议用 <code>--resume</code> 重试失败行后再冻结最终结果。"
        )
    else:
        conclusion = "所有记录均已成功推理，可作为当前版本的筛选结果使用。"

    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    total_tokens = sum(int(row.get("llm_total_tokens") or 0) for row in rows)
    system_prompt_html = esc(SYSTEM_PROMPT)
    system_prompt_cn_html = esc(SYSTEM_PROMPT_CN)
    user_prompt_html = esc(USER_PROMPT)
    user_prompt_cn_html = esc(USER_PROMPT_CN)

    return f"""<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>LLM 筛选结果总结{'（最终版）' if finalized else ''}</title>
<style>
:root {{ --bg:#f5f7f8; --panel:#fff; --ink:#17212b; --muted:#62707d; --line:#d9e1e6; --accent:#176b87; --good:#287846; --warn:#a76712; --bad:#b53c3c; --soft-blue:#e9f3f7; --soft-green:#eaf6ee; --soft-amber:#fff2d9; --soft-red:#faebeb; }}
* {{ box-sizing:border-box; }}
body {{ margin:0; color:var(--ink); background:var(--bg); font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif; line-height:1.5; }}
header {{ background:var(--panel); border-bottom:1px solid var(--line); }}
.header-inner,main {{ width:min(1180px,100%); margin:0 auto; padding:28px 24px; }}
h1 {{ margin:0 0 8px; font-size:30px; font-weight:750; letter-spacing:0; }}
h2 {{ margin:0; font-size:19px; letter-spacing:0; }}
h3 {{ margin:0 0 8px; font-size:15px; }}
.subtitle,.hint,.metric-note,.footer {{ color:var(--muted); font-size:13px; }}
.subtitle {{ margin:0; }}
.notice {{ margin-top:18px; padding:14px 16px; border:1px solid #e9c26e; border-left:4px solid var(--warn); border-radius:8px; background:var(--soft-amber); color:#6a470d; }}
section {{ margin:26px 0; }}
.section-head {{ display:flex; align-items:end; justify-content:space-between; gap:16px; margin-bottom:12px; }}
.hint {{ margin:4px 0 0; }}
.cards {{ display:grid; grid-template-columns:repeat(4,minmax(0,1fr)); gap:12px; }}
.grid-2,.method-grid {{ display:grid; grid-template-columns:repeat(2,minmax(0,1fr)); gap:12px; }}
.method-grid {{ grid-template-columns:1.1fr .9fr; }}
.card,.sample,.callout {{ background:var(--panel); border:1px solid var(--line); border-radius:8px; }}
.card {{ padding:16px; }}
.metric-label {{ color:var(--muted); font-size:12px; text-transform:uppercase; letter-spacing:.04em; }}
.metric-value {{ margin-top:8px; font-size:28px; font-weight:760; }}
.metric-note {{ margin-top:6px; }}
.bar {{ width:100%; height:8px; margin-top:10px; overflow:hidden; border-radius:99px; background:#e6ecf0; }}
.bar span {{ display:block; height:100%; background:var(--accent); }}
.bar .good {{ background:var(--good); }} .bar .warn {{ background:var(--warn); }} .bar .bad {{ background:var(--bad); }}
.callout-list,.samples {{ display:grid; gap:10px; }}
.callout {{ padding:13px 14px; border-left:4px solid var(--accent); }}
.callout.good {{ border-left-color:var(--good); }} .callout.warn {{ border-left-color:var(--warn); }} .callout.bad {{ border-left-color:var(--bad); }}
.callout strong {{ display:block; margin-bottom:3px; }}
table {{ width:100%; overflow:hidden; border:1px solid var(--line); border-radius:8px; border-collapse:separate; border-spacing:0; background:var(--panel); }}
th,td {{ padding:10px 11px; border-bottom:1px solid var(--line); text-align:left; vertical-align:middle; font-size:13px; }}
th {{ background:#edf2f5; color:#3e4b57; white-space:nowrap; font-weight:700; }}
tr:last-child td {{ border-bottom:0; }} .num {{ text-align:right; font-variant-numeric:tabular-nums; }}
.topic {{ display:block; max-width:430px; }}
.tag {{ display:inline-flex; min-height:22px; align-items:center; padding:2px 7px; border-radius:999px; background:#edf1f4; color:#34424e; font-size:12px; font-weight:650; }}
.tag.good {{ background:var(--soft-green); color:#1d6a39; }} .tag.warn {{ background:var(--soft-amber); color:#744a09; }} .tag.bad {{ background:var(--soft-red); color:#963232; }}
.method-list {{ display:grid; grid-template-columns:132px 1fr; gap:8px 12px; margin:12px 0 0; font-size:13px; }}
.method-list dt {{ color:var(--muted); font-weight:700; }} .method-list dd {{ margin:0; }}
.method-bullets {{ margin:12px 0 0; padding-left:18px; font-size:13px; }} .method-bullets li {{ margin:7px 0; }}
.prompt-card {{ margin-top:12px; }}
.prompt-box,.rule-box {{ margin:12px 0 0; padding:13px; overflow:auto; border:1px solid var(--line); border-radius:8px; background:#f8fafc; white-space:pre-wrap; font:12px/1.5 ui-monospace,SFMono-Regular,Menlo,Consolas,monospace; }}
.rule-box {{ font-family:inherit; font-size:13px; font-weight:700; }}
.sample {{ padding:13px; }} .sample-meta {{ margin-bottom:6px; color:var(--muted); font-size:12px; }} .sample-text {{ margin:6px 0; font-size:13px; }} .sample-link {{ margin-top:9px; font-size:13px; }} a {{ color:#0b5f7e; }}
.empty-state {{ padding:16px; border:1px solid var(--line); border-radius:8px; background:var(--panel); color:var(--muted); }}
.footer {{ margin-top:30px; }}
code {{ padding:1px 4px; border-radius:4px; background:#eef2f5; font-family:ui-monospace,SFMono-Regular,Menlo,Consolas,monospace; font-size:.92em; }}
@media (max-width:900px) {{ .cards,.grid-2,.method-grid {{ grid-template-columns:1fr; }} .header-inner,main {{ padding:22px 16px; }} table {{ display:block; overflow-x:auto; white-space:nowrap; }} .method-list {{ grid-template-columns:1fr; }} }}
</style>
</head>
<body>
<header><div class="header-inner">
<h1>LLM 筛选结果总结{'（最终版）' if finalized else ''}</h1>
<p class="subtitle">数据文件：{esc(pairs_path)}。生成时间：{esc(now)}。</p>
<div class="notice"><strong>结论：</strong>{conclusion}</div>
</div></header>
<main>
<section><div class="cards">
{metric_card("总记录数", fmt_number(total), "merged pairs 去重后的行数", "", None)}
{metric_card("成功推理", fmt_number(success), f"成功率 {pct(success, total)}", "good", pct_value(success, total))}
{metric_card("格式失败并排除" if finalized else "失败记录", fmt_number(failed), f"错误率 {pct(failed, total)}" if not finalized else "不纳入最终筛选结果", "bad", pct_value(failed, total))}
{metric_card("当前保留", fmt_number(kept), f"全量保留率 {pct(kept, total)}；成功口径 {pct(kept, success)}", "warn", pct_value(kept, total))}
</div></section>

<section><div class="section-head"><div><h2>关键判断</h2><p class="hint">当前数据完整性、筛选强度与应重点复核的规则边界。</p></div></div>
<div class="callout-list">
<div class="callout {'good' if finalized or failed <= max(1, total * 0.01) else 'bad'}"><strong>{'最终版已冻结' if finalized else ('运行完整性良好' if failed <= max(1, total * 0.01) else '运行完整性不足')}</strong>成功 {fmt_number(success)} 条，失败 {fmt_number(failed)} 条；失败率为 {pct(failed, total)}。{'这部分已按格式失败排除，不纳入最终结果。' if finalized else ('仅需重试失败行。' if failed else '无需补跑。')}</div>
<div class="callout"><strong>成功推理部分有效收紧样本</strong>成功推理 {fmt_number(success)} 条中保留 {fmt_number(kept)} 条，成功口径保留率为 {pct(kept, success)}。</div>
<div class="callout warn"><strong>立场标签存在需复核边界</strong>保留集中有 {fmt_number(neutral_kept)} 条的 stance_label 为 neutral_or_factual 或 unclear，占保留集 {pct(neutral_kept, kept)}。代码最终依据 has_stance=true，而不会额外要求标签必须是 support/oppose/mixed_or_complex。</div>
</div></section>

<section><div class="section-head"><div><h2>模型、Prompt 与筛选方法</h2><p class="hint">模型从结果字段读取；Prompt 与判定规则来自当前 <code>llm_post_filter.py</code>。</p></div></div>
<div class="method-grid">
<div class="card"><div class="metric-label">模型与请求设置</div><dl class="method-list">
<dt>结果中的模型</dt><dd>{esc(model_text)}</dd><dt>服务接口</dt><dd>vLLM OpenAI-compatible <code>/v1/chat/completions</code></dd>
<dt>推理模式</dt><dd>Qwen3 thinking 默认开启；温度 <code>0</code>；严格 JSON schema 输出</dd>
<dt>文本截断</dt><dd>最多 <code>{DEFAULT_MAX_POST_CHARS}</code> 个字符</dd><dt>最大输出</dt><dd><code>{DEFAULT_MAX_OUTPUT_TOKENS}</code> tokens</dd>
<dt>端点/GPU</dt><dd>结果文件不记录每行的 endpoint 或 GPU 分组，不能从结果反推历史运行拓扑。</dd></dl></div>
<div class="card"><div class="metric-label">输入给 LLM 的字段</div><ul class="method-bullets">
<li><code>platform</code>、<code>record_type</code></li><li><code>topic</code>：指定政策/公共议题</li>
<li><code>retrieval_keyword</code>：命中关键词</li><li><code>rule_label</code>、<code>rule_reason</code>：第一阶段规则结果</li>
<li><code>post_text</code>：帖子正文，必要时按字符上限截断</li></ul></div></div>
<div class="grid-2" style="margin-top:12px"><div class="card"><div class="metric-label">LLM 输出标签</div><ul class="method-bullets">
<li><strong>相关性：</strong><code>strongly_relevant</code>、<code>relevant</code>、<code>weak_keyword_match</code>、<code>off_topic</code>、<code>insufficient_context</code></li>
<li><strong>立场：</strong><code>has_stance</code> 与 <code>support</code>、<code>oppose</code>、<code>mixed_or_complex</code>、<code>neutral_or_factual</code>、<code>unclear</code></li>
<li><strong>讨论潜力：</strong><code>high</code>、<code>medium</code>、<code>low</code>、<code>unclear</code></li>
<li><strong>人工复核：</strong><code>needs_human_review</code> 为 true 或 <code>llm_error</code> 非空</li></ul></div>
<div class="card"><div class="metric-label">最终保留规则</div><pre class="rule-box">topic_relevance in strongly_relevant / relevant
AND has_stance = true
AND discussion_potential in high / medium</pre><p class="hint">代码会强制执行该规则；即使模型自身的 final_keep 与该规则不一致，也以规则计算出的 <code>llm_final_keep</code> 为准。</p></div></div>
<div class="grid-2"><div class="card prompt-card"><div class="metric-label">System Prompt 原文</div><pre class="prompt-box">{system_prompt_html}</pre></div><div class="card prompt-card"><div class="metric-label">System Prompt 中文译文</div><pre class="prompt-box">{system_prompt_cn_html}</pre></div></div>
<div class="grid-2"><div class="card prompt-card"><div class="metric-label">User Prompt 模板原文</div><pre class="prompt-box">{user_prompt_html}</pre></div><div class="card prompt-card"><div class="metric-label">User Prompt 模板中文译文</div><pre class="prompt-box">{user_prompt_cn_html}</pre></div></div>
</section>

<section><div class="grid-2"><div><div class="section-head"><div><h2>标签分布</h2><p class="hint">包含失败记录；失败记录的标签为无结果。</p></div></div>{table(["维度", "标签", "数量", "占全量"], label_rows, {2})}</div><div><div class="section-head"><div><h2>错误类型</h2><p class="hint">按错误类别聚合，便于确定补跑策略。</p></div></div>{error_block}</div></div></section>

<section><div class="section-head"><div><h2>平台表现</h2><p class="hint">成功口径保留率更能反映模型筛选强度。</p></div></div>{platform_table}</section>

<section><div class="section-head"><div><h2>Topic 保留量 Top 12</h2><p class="hint">按当前保留数量排序。</p></div></div>{topic_table(top_topics, True)}</section>

<section><div class="grid-2"><div><div class="section-head"><div><h2>成功口径保留率最高</h2><p class="hint">仅纳入至少 20 条成功记录的 Topic。</p></div></div>{topic_table(high_topics, False)}</div><div><div class="section-head"><div><h2>成功口径保留率最低</h2><p class="hint">仅纳入至少 20 条成功记录的 Topic。</p></div></div>{topic_table(low_topics, False)}</div></div></section>

<section><div class="section-head"><div><h2>成功记录的剔除原因组合</h2><p class="hint">根据最终保留规则反推，并非模型原文理由。</p></div></div>{table(["未通过的条件", "数量", "占成功记录"], [[esc(name), fmt_number(count), pct(count, success)] for name, count in removal_reasons.most_common()], {1})}</section>

<section><div class="section-head"><div><h2>建议</h2></div></div><div class="callout-list">
<div class="callout {'warn' if failed and not finalized else 'good'}"><strong>{'格式失败记录已排除' if finalized else ('补跑失败记录' if failed else '筛选运行已完整')}</strong>{('最终分析只使用成功推理结果；' + fmt_number(failed) + ' 条异常记录保留在 errors.csv 中供追溯。') if finalized else ('使用原始命令加 --resume 重试 ' + fmt_number(failed) + ' 条失败记录；现有成功结果会被跳过。' if failed else '当前没有 llm_error 记录。')}</div>
<div class="callout warn"><strong>审查中性/不清楚立场的保留记录</strong>优先检查下方“需关注的保留样本”。若研究只接受明确政策立场，可在后续规则中要求 stance_label 为 support、oppose 或 mixed_or_complex。</div>
<div class="callout"><strong>用于分析的文件</strong>保留结果：<code>llm_post_relevance_filtered.csv</code>；需人工复核：<code>llm_post_relevance_review.csv</code>；全部带标签结果：<code>llm_post_relevance_pairs.csv</code>。</div>
</div></section>

<section><div class="section-head"><div><h2>需关注的保留样本</h2><p class="hint">从当前保留且 stance_label 为 neutral_or_factual 或 unclear 的记录中固定随机抽取 {focus_count} 条；格式与审查样本一致。</p></div></div><div class="samples">{''.join(sample_card(row, index, translations) for index, row in enumerate(focus_rows, 1))}</div></section>

<section><div class="section-head"><div><h2>随机保留样本人工审查</h2><p class="hint">从当前 {fmt_number(kept)} 条保留记录中固定随机抽取 {audit_count} 条；随机种子 {AUDIT_SEED}。每条提供英文原文片段、中文译文和 LLM 理由。</p></div></div><div class="samples">{''.join(sample_card(row, index, translations) for index, row in enumerate(audit_rows, 1))}</div></section>

<p class="footer">报告由 <code>scripts/generate_llm_summary_report.py</code> 生成；当前累计 tokens：{fmt_number(total_tokens)}。</p>
</main></body></html>"""


def main() -> None:
    args = parse_args()
    if not args.pairs.exists():
        raise SystemExit(f"Pairs CSV not found: {args.pairs}")
    if not args.translations.exists():
        raise SystemExit(f"Translations JSON not found: {args.translations}")
    rows = dedupe_rows(read_csv(args.pairs))
    translations = json.loads(args.translations.read_text(encoding="utf-8"))
    if not isinstance(translations, dict):
        raise SystemExit("Translations JSON must be an object keyed by llm_row_key.")
    report = render_report(
        rows,
        args.pairs,
        translations,
        args.audit_samples,
        args.focus_samples,
        args.finalized,
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(report, encoding="utf-8")
    print(f"Wrote {args.output} from {len(rows):,} rows")


if __name__ == "__main__":
    main()
