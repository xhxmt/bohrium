---
name: scientific-pdf-parser
description: Layout-aware parsing of scientific PDFs (two/multi-column papers, Chinese or English, scanned pages) into verifiable structured results - reading order, section hierarchy, tables (multi-level headers, row/column spans, wrapped cells, tables continued across pages merged, image tables read by OCR), numbered equations, captions, footnotes, references, page headers/footers removed - where every block carries page + bbox locators, plus quote verification for answers and a result.json that keeps every output_contract field with the declared types. Use for any task that asks to parse / extract / structure a paper PDF or to answer questions about it with evidence, and to write /app/output/result.json.
---

# 科研 PDF 版面解析（scientific-pdf-parser）

把论文 PDF 解析成**可核验**的结构化结果：正确的阅读顺序（双栏/多栏/通栏/跨页续接）、章节层级、表格单元格（多层表头、跨列/跨行、单元格内换行、无题注表、跨页续表自动拼接、图片表格 OCR）、公式编号、图表题注、脚注、参考文献；页眉页脚/页码/页边水印从正文中剔除。每个区块都带 `页码 + bbox(PDF pt, 左上原点)`，答案中的引文由脚本逐字核对。

脚本在 `/skills/weekly-skill/scripts/`（下面的命令都写了完整路径，可直接复制；Skill 装在别处时把 `/skills/weekly-skill` 换成本 SKILL.md 所在目录）。只需 python3 标准库；有 PyMuPDF / pdfplumber / poppler(pdftotext) / tesseract / Pillow 时自动使用。

## 工作流程（按顺序，尽量少轮次）

**第 1 步 — 一条命令完成读题+解析+草稿（必须先做）**
```bash
python3 /skills/weekly-skill/scripts/run_case.py   # 默认读 /input/case.json 与 /input/task.md，输出到 /app/output
```
它会打印：任务原文、output_contract 全文（另存 `/app/work/contract.json`）与字段类型、契约要求生成的文件、找到的 PDF、解析摘要（章节树、表格清单、公式编号、警告），并**立即写出草稿** `/app/output/result.json`（status=partial），保证预算用完时也有诚实的可交付物。最后列出每个契约字段在草稿中的来源和类型：`UNFILLED` 的必须由你填写，`auto:` 的是通用解析结果，要核对是否就是题目要的内容。
- 题目限定页码时：加 `--pages 3-7` 重跑。扫描件自动 OCR，逐页选语言（`--ocr force` 可对全部页强制 OCR，`--ocr-lang chi_sim+eng` 指定语言）。
- case.json 指定了材料时只解析这些；其余输入文件列在 `OTHER input files`，需要时用 `--material 路径` 加入。多份材料时 `query.py` 默认查全部文档（`query.py docs` 列出，`--doc 文件名` 选一份）。
- 解析产物（`/app/output/parsed/`）：`document.md`（阅读顺序全文，每块前有 `<!-- 块id 页 [bbox] 类型 -->` 锚点）、`document.json`（全部结构）、`parse_report.md`（自检：与独立提取器的文字覆盖率、表/图/公式/文献编号连续性、可疑断句、OCR 置信度）、`tables/*.csv|html|md`、`crops/*.png`（表格/公式/图截图）、`previews/page-*.png`（阅读顺序标注图）。

**第 2 步 — 按任务取证据（不要整篇 cat；用查询；一次 bash 调用里用 `;` 串起多条查询，省轮次）**
```bash
Q="python3 /skills/weekly-skill/scripts/query.py"
$Q overview                                     # 一条命令看全貌：标题、作者/单位/邮箱（带 bbox）、摘要、章节树、表/图/公式/脚注清单、自检
$Q table 1; $Q table 2; $Q search "GPU|K80"     # 多条查询合并成一次调用
python3 /skills/weekly-skill/scripts/query.py outline                 # 章节树 + 块 id
python3 /skills/weekly-skill/scripts/query.py search "关键词|regex"    # 命中块/表格行/图内文字/文献（最多 --max 条，默认 15），带 [块id 页 bbox 类型]；含括号等字符时按原文搜索（或加 --literal）
python3 /skills/weekly-skill/scripts/query.py show p5-b3 --context 1  # 块全文、逐行 bbox、跨栏续接关系（也可 show table-2 / eq-3 / ref-12）
python3 /skills/weekly-skill/scripts/query.py table 2                 # 表格 Markdown（table-2 / 2 / 表2 均可；--csv 输出 CSV）；跨页续表会给出拼接后的 CSV
# 其他：page 4 | section 3.1 | equations | figures | footnotes | refs 12 | furniture | checks | docs
python3 /skills/weekly-skill/scripts/query.py ocr 3 --bbox 60,330,300,560   # 扫描件局部重识别（PDF pt 坐标，同 show 输出）；--dpi 300 --lang eng --psm 6
```
扫描件需要复核某处文字时用 `query.py ocr`，**不要自己调用 `tesseract`**：限核容器里裸跑 tesseract 会多线程空转，一次调用可能卡几分钟、耗尽 900 秒预算（必须直接调用时先 `export OMP_THREAD_LIMIT=1`）。OCR 结果已有逐块 `ocr_conf`，置信度够高的块不必重识别。
短文档（< 8 页）可直接读 `document.md`。数值一律从表格单元格/原文块复制，不要凭记忆或推测。表格 `issues` 里有 `repaired:`（换行合并、跨行标签）或 `body_source: ocr…`（图片表格 OCR）时，能看图就对照 `crops/table-N.png` 确认关键数值；不能看图就以文字层 / CSV 为准，并在 `uncertainties` 中写明“未对照截图核对”。

**第 3 步 — 写 `/app/work/answers.json`，然后收尾校验**
```json
{
  "status": "success | partial | cannot_determine | refused | failed",
  "status_reason": "一句话说明",
  "fields": {"<contract 字段>": "按契约要求的结构和类型给出的值（优先于一切自动值，脚本不会覆盖）"},
  "answers": [{"question": "...", "answer": "...",
               "evidence": [{"block": "p7-b0", "quote": "从 PDF 原样复制的短句或表格行"}],   # 多份 PDF 时加 "file": "文件名"
               "confidence": "high|medium|low"}],
  "uncertainties": ["无法确认/可能有误之处及原因"],
  "not_reported": ["原文未报告的内容"]
}
```
```bash
python3 /skills/weekly-skill/scripts/finalize.py   # 合并答案→/app/output/result.json + result_summary.md，逐条核对引文
```
契约字段内容很多（整张表、几十条记录、长文本）时，不要在一次回复里手写整个 JSON（单次回复上限 16384 token）：写一段简短的 python3 脚本，从 `/app/output/parsed/document.json`（`tables[].matrix`、`sections`、`blocks`）取值生成 `/app/work/answers.json`。
若输出 `CHECK:`（引文未在原文找到、契约字段缺失/为空/类型不符、契约要求的文件不存在），修正后再运行一次；`REVIEW:` 行列出自动填充的字段，确认它们确实回答了契约的要求，否则在 `fields` 中改写。`block` 可填块 id（`p3-b12`）或对象 id（`table-2`、`eq-3`、`figure-1`、`ref-12`）。

**按任务类型的做法**（证据一律带 `block` + 逐字 `quote`）
- 结构化解析（标题/作者/章节/表格/公式/参考文献…）：契约字段多数已由草稿自动填好（`metadata.authors` 已按版面拆出姓名、单位、邮箱并注明 `affiliation_rule`）；先看 run_case 的 `auto:` 清单，只改错的、补 `UNFILLED` 的，不要重新手抄整篇。
- 问答 / 抽取：每个问题一条 `answers[]`，数值从表格单元格或原文块复制；原文没有 → `not_reported`，不要推测。
- 翻译 / 总结 / 知识结构化：脚本不会自动填这类字段（`translated_*`、`summary`、`knowledge_*` 等一律 `UNFILLED`），由你写；按块或按节对应原文，每段/每个节点给出 `page` + `block`，章节编号、公式编号、表号、数值、单位原样保留，术语前后一致。
- 核验 / 多材料对比：两份材料各自引用（`file` + `block` + `quote`）；冲突就两边都列出并写进 `uncertainties`，无法判断写 `cannot_determine`。

## 规则
- **要完成的是本案例的解析/问答任务**：以 `case.json`（任务、材料、output_contract）为准。若 `task.md` 里是面向 Skill 作者的说明（如“编写 SKILL.md”“提交到 /app/submission”），那不是本次要做的事：不要创建或修改 Skill 文件、`/app/submission`，只把结果写到 `/app/output`。
- **只写原文中有的内容**。找不到 → 写入 `not_reported` 或 status=`cannot_determine`，不要补造；材料冲突时两处都引用并说明。
- 引文 `quote` 必须逐字来自 PDF（从 `query.py show` 复制）；可以跨栏/跨页续接，中间省略的部分写 `...`；释义放在 `answer` 里。
- 契约要求特定文件（如 `parsed.md`、`tables.csv`）时，从 `/app/output/parsed/` 复制或转换生成到 `/app/output/` 并在对应字段写路径；run_case 报告 `MATERIALS: NONE` 时先 `ls -R /input` 找文件，再用 `--material 路径` 重跑。
- 契约字段：草稿只自动填了能从解析结果直接得到的字段（标题、章节、表格、公式、参考文献、页数、证据、不确定项、修改记录等，按契约声明的类型）。题目特有的字段（答案、判断、抽取结果、翻译等）以及标为 `UNFILLED` 的字段必须由你在 `fields` 中给出；契约规定了结构（例如 `answers` 是“题号→答案”的对象）就严格按它写。你在 `fields` 中给的值不会被覆盖。
- 解析结果的已知边界见 `parse_report.md`：表格只有题注无表体（可能是图片表格）、OCR 低置信词、可疑断句块——在 `uncertainties` 中如实列出，不要掩盖。
- 异常：加密 PDF / 损坏文件 → run_case 打印 FAILED 原因，status=`failed` 并写明人工接管方式；无文字层且无 OCR 引擎 → status=`partial`，列出需 OCR 的页；脚本崩溃 → 用 `python3 /skills/weekly-skill/scripts/parse_pdf.py PDF --out /app/output/parsed --backend poppler` 重试，再不行用 `pdftotext -layout` 兜底并在 status_reason 说明。
- 预算：64 次模型调用 / 900 秒。目标 ≤ 15 轮：第 1 步 1 轮，查证 3–8 轮，写 answers + finalize 2–3 轮。不要修改脚本，不要重复解析同一文件。
- 不要另写脚本从 PDF 重新提取表格、重建 `document.md` 或重新排版结果（这些已在 `/app/output/parsed/`，契约要的文件直接复制）；某个单元格或字段确有错误时，只在 `fields` 里改那一处并在 `uncertainties` 写明依据。用到 30 轮左右还没收尾时，立即写 answers.json 并运行 finalize。

## 交付前自检（finalize 之后）
finalize 输出没有 `CHECK:`；没有意外的 `UNFILLED`；`REVIEW:` 列出的自动字段确实回答了题目；契约要求的文件都在 `/app/output/`；`status` 与实际完成度一致（全部完成才写 success）。

## 最终回复
用 3–6 行说明：status、输出位置（`/app/output/result.json`、`/app/output/parsed/document.md`）、关键结论及其 `页码/块id`、主要不确定项。

更多说明（方法、输出字段、局限、复用方式）：`README.md`、`docs/OUTPUT_SCHEMA.md`；示例：`examples/`（示意，非正式案例结果）。
