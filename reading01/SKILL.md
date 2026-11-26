---
name: scientific-pdf-parser
description: Layout-aware parsing of scientific PDFs (two/multi-column papers, Chinese or English, scanned pages) into verifiable structured results - reading order, section hierarchy, tables with multi-level headers, numbered equations, captions, footnotes, references, page headers/footers removed - where every block carries page + bbox locators, plus quote verification for answers. Use for any task that asks to parse / extract / structure a paper PDF or to answer questions about it with evidence, and to write /app/output/result.json.
---

# 科研 PDF 版面解析（scientific-pdf-parser）

把论文 PDF 解析成**可核验**的结构化结果：正确的阅读顺序（双栏/多栏/通栏/跨页续接）、章节层级、表格单元格（含多层表头/跨列）、公式编号、图表题注、脚注、参考文献；页眉页脚/页码/页边水印从正文中剔除。每个区块都带 `页码 + bbox(PDF pt, 左上原点)`，答案中的引文由脚本逐字核对。

脚本目录：`SKILL_DIR=/skills/weekly-skill`（若不存在，用本 SKILL.md 所在目录）。只需 python3 标准库；有 PyMuPDF / pdfplumber / poppler(pdftotext) / tesseract / Pillow 时自动使用。

## 工作流程（按顺序，尽量少轮次）

**第 1 步 — 一条命令完成读题+解析+草稿（必须先做）**
```bash
python3 $SKILL_DIR/scripts/run_case.py            # 默认读 /input/case.json 与 /input/task.md，输出到 /app/output
```
它会打印：任务原文、output_contract 字段、找到的 PDF、解析摘要（章节树、表格清单、公式编号、警告），并**立即写出草稿** `/app/output/result.json`（status=partial），保证预算用完时也有诚实的可交付物。
- 题目限定页码时：加 `--pages 3-7` 重跑。扫描件自动 OCR（`--ocr force` 可对全部页强制 OCR，`--ocr-lang chi_sim+eng` 指定语言）。
- 解析产物（`/app/output/parsed/`）：`document.md`（阅读顺序全文，每块前有 `<!-- 块id 页 [bbox] 类型 -->` 锚点）、`document.json`（全部结构）、`parse_report.md`（自检：与独立提取器的文字覆盖率、表/图/公式/文献编号连续性、可疑断句、OCR 置信度）、`tables/*.csv|html|md`、`crops/*.png`（表格/公式/图截图）、`previews/page-*.png`（阅读顺序标注图）。

**第 2 步 — 按任务取证据（不要整篇 cat；用查询）**
```bash
Q="python3 $SKILL_DIR/scripts/query.py"
$Q outline                 # 章节树 + 块 id
$Q search "关键词|regex"    # 命中块/表格行/文献，带 [块id 页 bbox 类型]
$Q show p5-b3 --context 1  # 块全文、逐行 bbox、跨栏续接关系
$Q table table-2           # 表格 Markdown（--csv 输出 CSV）
$Q page 4 | $Q section 3.1 | $Q equations | $Q figures | $Q footnotes | $Q refs 12 | $Q furniture | $Q checks
```
短文档（< 8 页）可直接读 `document.md`。数值一律从表格单元格/原文块复制，不要凭记忆或推测。

**第 3 步 — 写 `/app/work/answers.json`，然后收尾校验**
```json
{
  "status": "success | partial | cannot_determine | refused | failed",
  "status_reason": "一句话说明",
  "fields": {"<contract 字段>": "按题目要求的值（覆盖自动填充值）"},
  "answers": [{"question": "...", "answer": "...",
               "evidence": [{"block": "p7-b0", "quote": "从 PDF 原样复制的短句或表格行"}],
               "confidence": "high|medium|low"}],
  "uncertainties": ["无法确认/可能有误之处及原因"],
  "not_reported": ["原文未报告的内容"]
}
```
```bash
python3 $SKILL_DIR/scripts/finalize.py   # 合并答案→/app/output/result.json + result_summary.md，逐条核对引文
```
若输出 `CHECK:`（引文未在原文找到、契约字段为空），修正 answers.json 后再运行一次。`block` 可填块 id（`p3-b12`）或对象 id（`table-2`、`eq-3`、`figure-1`、`ref-12`）。

## 规则
- **只写原文中有的内容**。找不到 → 写入 `not_reported` 或 status=`cannot_determine`，不要补造；材料冲突时两处都引用并说明。
- 引文 `quote` 必须逐字来自 PDF（从 `query.py show` 复制）；释义放在 `answer` 里。
- 契约要求特定文件（如 `parsed.md`、`tables.csv`）时，从 `/app/output/parsed/` 复制或转换生成到 `/app/output/` 并在对应字段写路径；run_case 报告 `MATERIALS: NONE` 时先 `ls -R /input` 找文件，再用 `--material 路径` 重跑。
- 草稿已由 run_case 自动填好全部契约字段（章节、表格、公式、参考文献、证据、不确定项、修改记录等）；你只需核对关键项并在 `fields` 中修正或补充题目特有的字段。
- 解析结果的已知边界见 `parse_report.md`：表格只有题注无表体（可能是图片表格）、OCR 低置信词、可疑断句块——在 `uncertainties` 中如实列出，不要掩盖。
- 异常：加密 PDF / 损坏文件 → run_case 打印 FAILED 原因，status=`failed` 并写明人工接管方式；无文字层且无 OCR 引擎 → status=`partial`，列出需 OCR 的页；脚本崩溃 → 用 `python3 $SKILL_DIR/scripts/parse_pdf.py PDF --out /app/output/parsed --backend poppler` 重试，再不行用 `pdftotext -layout` 兜底并在 status_reason 说明。
- 预算：64 次模型调用 / 900 秒。目标 ≤ 15 轮：第 1 步 1 轮，查证 3–8 轮，写 answers + finalize 2–3 轮。不要修改脚本，不要重复解析同一文件。

## 最终回复
用 3–6 行说明：status、输出位置（`/app/output/result.json`、`/app/output/parsed/document.md`）、关键结论及其 `页码/块id`、主要不确定项。

更多说明（方法、输出字段、局限、复用方式）：`README.md`、`docs/OUTPUT_SCHEMA.md`；示例：`examples/`（示意，非正式案例结果）。
