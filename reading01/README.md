# scientific-pdf-parser · 科研 PDF 版面解析 Skill（阅读01｜文献文档解析）

把一篇科研论文 PDF 解析成**可直接继续处理、且每一处都能指回原文**的结构化结果：

- **排得对**：自动判断单栏/双栏/三栏，通栏标题、通栏图表把页面切成若干带，带内逐栏阅读；跨栏、跨页的段落自动接续并记录接续关系。
- **提得全**：页眉、页脚、页码、页边竖排水印、审稿行号从正文流中剔除，但保留在 `furniture` 列表中可查。第 1 页的作者注（∗ † ‡）和会议/预印本说明行归为脚注，不混进摘要；一个块里堆叠的多条脚注逐条拆开。APS / Elsevier 等 Advent 排版的符号字体（没有 ToUnicode 映射）中的 `þ ¼ ð Þ ½` 还原为 `+ = ( ) [`，并记入修改记录。断行连字符按“全文是否出现过带连字符的写法”决定保留还是去掉，每一处都有记录。连字（ﬁ/ﬂ）会展开。
- **拆得清**：标题、**作者—单位—邮箱**（按作者网格的列、上标标记或唯一单位关联，每项带 bbox 与关联依据；拿不准的不关联）；章节层级（1 / 1.1 / A.1 / I. / 一、/ 第X章）；表格按单元格还原，支持多层表头、跨列、**跨行（`\multirow`，居中或顶对齐）**、**单元格内换行**、分组行，**无题注的有线表**，**跨页续表自动拼成一张**（“Table 3 (continued)”、“续表3”或下一页顶部同列数的无题注表），**图片形式的表格用 OCR 读出单元格**，输出 CSV/HTML/Markdown；带编号的行间公式；上下标（`<sup>/<sub>`）；图表题注；脚注；参考文献逐条切分（支持编号式与悬挂缩进式）。
- **核得回**：每个区块都带 `页码 + bbox`。`document.md` 每段前有定位锚点。表格、公式、图有截图，每页有阅读顺序标注图。答案中的引文会被逐字核对，并定位到具体的块或表格行（跨栏/跨页接续的句子、带 “...” 省略的引文、空格或标点略有出入的引文都能核对，并注明匹配方式）。
- **交付契约**：`result.json` 保留 `output_contract` 的全部字段；Agent 在 `answers.json` 中填写的值永远优先，脚本内置的同名部分不会覆盖它（转存到 `skill_details`）；契约声明了类型（JSON Schema 或 “string/list/object” 描述）时，自动填充的值按类型转换，转不了就留空并提示 Agent 填写；`finalize.py` 校验字段是否存在、类型是否匹配、契约要求的文件是否已生成。
- **自检**：与另一套独立提取器对比文字覆盖率，检查表号、图号、公式号、参考文献编号是否连续，列出疑似断句，报告 OCR 置信度。所有问题都写进 `parse_report.md` 和 `result.json.uncertainties`。

## 为什么需要它（基础 Agent 的典型失败）

基础 Agent 常用 `pdftotext` 或 `PyMuPDF.get_text()` 直接取全文，常见问题有：两栏文字串在一起；段落被页眉页脚、浮动体打断；表格被拍平成一串数字；跨页段落断开；结论没有页码和位置；扫描件直接得到空文本。本 Skill 用脚本完成这些确定性工作，模型只负责理解任务、查证据、写答案。

## 快速开始

```bash
SKILL_DIR=/skills/weekly-skill            # 或本仓库目录
python3 $SKILL_DIR/scripts/run_case.py     # 读 /input/case.json，解析 → /app/output/parsed/，写草稿 result.json
python3 $SKILL_DIR/scripts/query.py outline
python3 $SKILL_DIR/scripts/query.py search "SQuAD|F1"
# 写 /app/work/answers.json（格式见 SKILL.md），然后：
python3 $SKILL_DIR/scripts/finalize.py     # 合并、核对引文、补齐 output_contract 字段 → /app/output/result.json
```

单独解析一个 PDF（不依赖评测目录）：

```bash
python3 scripts/parse_pdf.py paper.pdf --out paper_parsed [--pages 3-7] [--backend auto|pymupdf|pdfplumber|poppler] [--ocr auto|off|force] [--ocr-lang chi_sim+eng]
python3 scripts/parse_pdf.py --check-env   # 查看本机可用引擎
```

## 依赖（全部可选，自动探测）

| 组件 | 作用 | 缺失时 |
|---|---|---|
| Python ≥ 3.7（仅标准库） | 全部逻辑 | 必需 |
| PyMuPDF（`pymupdf`/`fitz`） | 首选后端：字符级坐标、字号、粗体、基线（上下标）、图片框、表格横线、矢量图区域；渲染截图 | 依次改用 pdfplumber → poppler |
| pdfplumber | 后端 2：词级坐标、字号、字体、表格横线 | 跳过 |
| poppler-utils（`pdftotext`、`pdftoppm`） | 后端 3：词级坐标；渲染；独立对照提取 | 用 pypdf 兜底（无坐标，仅文本，会在结果中标明） |
| tesseract（`eng`、`chi_sim`） | 扫描页/乱码页 OCR（预处理：灰度、自动对比度、中值去噪、Otsu 二值化、低分辨率放大；后处理：低置信词清单、数字串中 O→0、l/I→1 校正并记录） | 页面标记为 `no_text_layer_unreadable`，status=partial，并列出人工接管页 |
| Pillow / numpy | OCR 预处理、预览图绘制 | 跳过对应步骤 |

全部处理在本地完成，不联网，不上传任何文件。测试环境：Python 3.13，PyMuPDF 2.2，pdfplumber 0.11，poppler 25.03，tesseract 5.5.0（`eng`、`chi_sim`）。代码按 Python 3.7 语法编写。

## 输出

`/app/output/`
- `result.json`：任务结果。先是 `case_id`、`status`（success/partial/cannot_determine/refused/failed）、`status_reason` 和**全部 output_contract 字段**（取值优先级：Agent 在 `answers.json.fields` 中的值 > 同名的内置部分 > 按字段名从解析结果自动填充；自动值按契约声明的类型转换），然后是内置部分：`answers`（每条附证据与引文核对结果）、`sources`（输入文件 sha256 与全部证据定位）、`uncertainties`、`not_reported`、`change_records`、`manual_takeover`、`materials`、`structure_summary`、`contract`（每个字段的来源、期望类型与实际类型、未填字段、类型问题、契约要求的文件）、`run`；若 Agent 的契约字段与内置部分同名，内置部分转存到 `skill_details`。
- `result_summary.md`：人可读摘要。
- `parsed/document.md`：按阅读顺序排列的全文。每块前有 `<!-- p3-b12 p3 [x0,y0,x1,y1] paragraph -->` 锚点；跨栏、跨页段落合并输出并注明 `continues across A -> B`；表格内嵌 Markdown；公式带编号和截图链接。
- `parsed/document.json`：完整结构（字段见 `docs/OUTPUT_SCHEMA.md`）。
- `parsed/parse_report.md`：自检报告。
- `parsed/tables/table-N.{csv,html,md}`（跨页续表另有 `table-N-merged.{csv,md}`；HTML 中跨行/跨列单元格带 `rowspan`/`colspan`，CSV/Markdown 中跨行标签在每行重复）、`parsed/crops/{table,figure,eq}-N.png`、`parsed/previews/page-NNN.png`（彩框为区块，数字为阅读顺序，灰框为剔除的页眉页脚）。

坐标约定：PDF 点（1/72 英寸），原点在页面左上角；块 id `p{页}-b{序号}`，序号即本页阅读顺序。

## 方法（流水线）

1. **取词**：按 PyMuPDF → pdfplumber → poppler → pypdf 的顺序选择后端，统一成“带坐标、字号、字体、粗体的词”。逐页检查文字层（字符数、`(cid:..)`/私用区/替换符比例），无文字层或乱码页转 OCR。
2. **行内修补**：把被 PDF 拆成独立行的堆叠上下标（如 410<sup>+160</sup><sub>−180</sub>）挂回宿主行；行内按大间隙切成片段。
3. **页面杂项**：跨页重复且位置相近的页眉页脚（数字归一化后比较）、页码（含“第 N 页”）、页边竖排文字、审稿行号；只出现一次、无法靠重复确认的期刊行 / DOI / arXiv 号 / 版权行（短文档、首页），按“位于页边小字 + 关键词”剔除并注明规则。
4. **分栏**：用窄行的 x 覆盖直方图找栏缝。要求正文以窄行为主、栏缝两侧都有足够的长行，并参考整篇文档的多数结果；居中于页面中线的短行（作者、单位）属于通栏区，不计入栏缝遮挡。对于左右栏被提取器粘在一起的中文行，在栏缝处切开。
5. **浮动体**：识别题注（排除正文中位于行首的 “Table 3 shows”；支持 “(continued)”、“续表”），题注在遇到表格横线或分列的表头行时结束。在题注两侧寻找表格行：以其他题注、正文行为边界；横线群向下延伸，只要两线之间仍是表格行（长表的中线与底线可能相隔几十行），只有顶线+中线、本页没有底线时视为跨页续表；表比栏宽时按紧邻题注的横线放宽查找范围。单元格用“多数行留空的 x 区间”定列（一行两端对齐文字被拉开的空隙不会单独决定分列），跨列单元格记为 colspan，按横线或数值行判断表头行数。之后做两项修补并逐条记录：单元格内换行（下一行只续写上一行的文字列：小写开头、上一行以连字符/介词结尾，或中文填满列宽）合并回原单元格；跨行标签按分组横线（一组只有一个标签则覆盖整组）、居中（上下取相同行数）或顶对齐（向下填充）分配 rowspan，拿不准的不分配。没有题注的表格：两条以上横线之间、按列对齐的多行文字（排除公式、图内文字、算法框）。图片表格：题注旁的嵌入图片（或文字层中的空白区）渲染后用 tesseract 读出，再走同样的分列流程。跨页续表：同一表号、续表题注，或下一页顶部同列数的无题注表，标记 `continued_from` 并拼成一张（重复表头去掉）。图的区域用图片框、矢量图聚类，或“题注上方的非正文行”确定，图内文字单独存放。
6. **阅读顺序**：通栏元素把页面切成带，带内按栏从左到右、每栏自上而下。
7. **成块与分类**：同一行的片段合并；依据行距、字号变化、首行缩进、句末标点、居中的数学行切分段落（通栏段落最后一行短到落进某一栏时仍并回该段）。分类出标题、作者单位、摘要（含无标签摘要的推断；中文按字数而非空格计长度）、章节标题（编号模式、粗体、字号排序）、列表、公式、脚注、参考文献。跨栏/跨页接续的条件：前段未以句末标点结束，后段以小写字母或中文开头且不缩进。
8. **OCR**：每页先用上一页选定的语言，置信度低于 75 时再试其他语言包，所以“英文封面 + 中文正文”的扫描件每页都用合适的模型。
9. **输出与自检**：见上文。

## 本地对照评测（可复现，非平台成绩）

以下数字均为本地运行结果，不是平台成绩。第 0 节是 Agent 端到端对照（2026-11-27/28），第 1–3 节是解析器本身（第 1 节为 v1.1.0 时的数字，v1.3 在这两份样例上的输出与 v1.1/v1.2 逐块一致）。

### 0. Agent 端到端对照：基础 Agent vs 加载本 Skill（`bench/agent_sim/`）

平台的 DeepSeek Harness 与正式案例不公开，`bench/agent_sim/` 按题面写明的运行约定自建了一个近似环境：Docker 容器（poppler、tesseract eng/chi_sim、PyMuPDF、pdfplumber），`/input/case.json` + `task.md`，Skill 只读挂载在 `/skills/weekly-skill`；预算与题面相同（64 次模型请求 / 900 秒 / 每次回复 16384 token）；模型 DeepSeek `deepseek-flash`，6 路并发，每个条件重复 3 次。基础 Agent 与 Skill Agent 只差“是否挂载 Skill”，系统提示、工具（bash / read_file / write_file）、案例输入完全相同。3 个模拟案例用公开 arXiv 论文构造：A = Transformer（1706.03762）整篇结构化（标题、作者单位、摘要、章节、表格、公式、图、脚注、参考文献，契约要求 `document.md`）；B = ResNet（1512.03385）表格问答 + 未报告项（GPU 型号原文未写）；C = BERT（1810.04805）前 3 页渲染成无文字层的扫描件，OCR + 问答。`check.py` 用作者自己写的标准答案逐项核对 result.json（不是官方白板）。

| 案例 | 条件 | 通过检查 | status=success | 平均请求数 | 平均用时 | 首个 result.json |
|---|---|---|---|---|---|---|
| A 双栏结构化 | 基础 Agent | 33%（2/3 次用完 64 次请求，没有 result.json） | 1/3 | 63.7 | 669 s | 352 s 或没有 |
| A | Skill v1.2 | 100% | 3/3 | 46.0 | 244 s | 6–7 s |
| A | **Skill v1.3** | **100%** | **3/3** | **29.3** | **210 s** | **7–8 s** |
| B 表格问答 | 基础 Agent | 100% | 3/3 | 53.3 | 438 s | 270–436 s |
| B | Skill v1.2 | 100% | 3/3 | 19.7 | 106 s | 5–7 s |
| B | **Skill v1.3** | **100%** | **3/3** | **23.3** | **97 s** | **7 s** |
| C 扫描件 OCR | 基础 Agent | 0%（3/3 次 900 秒超时，没有 result.json） | 0/3 | 22.0 | 903 s | 没有 |
| C | Skill v1.0 | 78% | 1/3 | 12.7 | 780 s | 约 490 s（tesseract 多线程空转） |
| C | Skill v1.2 | 100% | 2/3（1 次 partial） | 30.3 | 226 s | 13–15 s |
| C | **Skill v1.3** | **100%** | **3/3** | **29.0** | **209 s** | **13–16 s** |

基础 Agent 的典型失败：A 中逐页手写 PyMuPDF 脚本拼表格，请求数耗尽时仍未写出 result.json；C 中自己调用 tesseract，在限核容器里多线程空转直到 900 秒超时。v1.3 相对 v1.2：A 案例请求数降 36%（最多一次 33 次，v1.2 最多 50 次，离 64 次上限更远），9 次运行全部 success。每个条件只有 3 次重复，同一版本的请求数在不同批次之间可相差 10 次以上（v1.3 定稿前的一批中 A 案例平均 43.3 次，随后在 SKILL.md 中加入“不要另写脚本重建解析结果”后为 29.3 次），这些数字只说明量级与方向。原始运行记录（每次的 trace.jsonl、result.json）不随 Skill 提交（体积大且含模型输出），可用 `bench/agent_sim/run_matrix.sh` 复现；复现步骤见 `bench/agent_sim/RESULTS.md`。

### 1. 标准样例（`bench/make_bench.py` + `bench/score_bench.py`）

`make_bench.py` 用 xelatex 生成中英文两份双栏测试论文（页眉页脚、跨栏跨页段落、多层表头表格、编号公式、脚注、参考文献），同时生成精确的标准答案；`score_bench.py` 按标准答案打分：

| 测试文档 | 方法 | 段落完整 | 段落顺序 | 页眉页脚混入 | 标题层级 | 表格单元格 | 公式编号 |
|---|---|---|---|---|---|---|---|
| 中文双栏 | `pdftotext` | 24% | 90% | 3 | — | 0%¹ | — |
| 中文双栏 | `pdftotext -layout` | 0% | 90% | 3 | — | 100%¹ | — |
| 中文双栏 | PyMuPDF `get_text()` | 95% | 100% | 3 | — | 0%¹ | — |
| 中文双栏 | **本 Skill（PyMuPDF 后端）** | **100%** | **100%** | **0** | **100%** | **100%** | **100%** |
| 中文双栏 | 本 Skill（仅 poppler） | 90% | 100% | 0 | 100% | 100% | 100% |
| 英文双栏 | `pdftotext` | 67% | 90% | 6 | — | 0%¹ | — |
| 英文双栏 | `pdftotext -layout` | 14% | 90% | 6 | — | 100%¹ | — |
| 英文双栏 | PyMuPDF `get_text()` | 81% | 100% | 6 | — | 0%¹ | — |
| 英文双栏 | **本 Skill（PyMuPDF 后端）** | **100%** | **100%** | **0** | **100%** | **100%** | **100%** |
| 英文双栏 | 本 Skill（仅 poppler） | 95% | 100% | 0 | 100% | 80%⁴ | 100% |
| 英文双栏（扫描件²） | 本 Skill，OCR（PyMuPDF 渲染） | 86% | 100% | 0 | 100% | 80% | 0% |
| 英文双栏（扫描件²） | 本 Skill，OCR（pdftoppm 渲染） | 81% | 100% | 0 | 100% | 60% | 100% |
| 中文双栏（扫描件²） | 本 Skill，OCR（PyMuPDF 渲染） | 0%³ | 70% | 0 | 62% | 0% | 50% |
| 中文双栏（扫描件²） | 本 Skill，OCR（pdftoppm 渲染） | 0%³ | 65% | 0 | 88% | 20% | 0% |

¹ 纯文本没有单元格结构，这里按“一行内按顺序出现整行数值”宽松计分。² 把上面两份 PDF 用 `pdftoppm -r 200 -gray` 渲染后合成纯图片 PDF（无文字层），由 tesseract 5.5.0（`eng`、`chi_sim`）识别；纯文本提取器在扫描件上输出为空（各项均为 0），不列出。³ “段落完整”要求整段逐字一致；中文正文逐字相似度约 94–96%，几乎每段都有 tesseract 中文模型认错的字（如“偶然”→“倘然”），所以这一项为 0，但分段与阅读顺序基本正确。两种渲染的差异来自页面渲染器不同。⁴ 英文样例的表格比栏宽，最后一列伸出栏外；poppler 不提供表格横线，无法据此放宽查找范围，最后一列丢失（PyMuPDF / pdfplumber 后端正常）。

v1.1.0 修正了英文样例生成器：表头与两条参考文献原为中文，而英文 PDF 使用纯拉丁字体，这些字在 PDF 中显示为空白，标准答案与 PDF 不一致；因此英文行的数字与 v1.0 的 README 不可直接比较。在同一份新样例上，v1.0 的 PyMuPDF 后端为 95% / 100% / 0 / 100% / 80% / 100%（表头行被并入题注、伸出栏外的最后一列丢失），v1.1 为全部 100%；两份扫描件上 v1.0 与 v1.1 的分数完全相同。

OCR 后处理的两点改进（相对直接使用 tesseract 的行框）：字号按“整行墨迹高度”（英文）或“词框高度中位数”（中文）估计，并在 tesseract 段落内统一，避免把正文行误判为标题、把脚注拼进正文；中文语境中的半角标点（`, : ; ( ) ? !`）还原为全角。

### 2. 难例自检（`bench/make_hard_cases.py` + `bench/selftest.py`）

`make_hard_cases.py` 生成五份带标准答案的难例：跨行表格（`\multirow` 居中奇数行、居中偶数行、顶对齐）、单元格内换行的 `p{}` 列、无题注的有线表、跨页的 longtable（第二页题注为 “(continued)”，表头重复）、只有图片的表格；无“摘要”标签的中文双栏论文；只有一页、期刊行/DOI/版权行都只出现一次的短文档；第 1 页英文、第 2–3 页中文的纯图片扫描件；v1.3 新增 `front_matter`：作者网格（姓名 / 单位 / 邮箱三行对齐）、摘要下方的 ∗ † 作者注与预印本说明行、`82.3±0.4`、`80.6/80.1` 形式数值的跨行分组表。`selftest.py` 逐项检查，另外检查 result.json 的契约处理（类型、扁平契约、契约中的输出文件名、翻译/总结类字段不被原文填充、case.json 其余键随任务显示）、引文核对（含块号写错时的定位纠正）和多文档证据定位（共 31 项）：

| 版本 | PyMuPDF 后端 | 仅 poppler |
|---|---|---|
| v1.0 | 4 / 22 通过 | 4 / 22 通过，1 项跳过 |
| v1.1 / v1.2 | 22 / 22 通过（新 31 项中 22 项通过） | 21 / 22 通过，1 项跳过 |
| **v1.3** | **31 / 31 通过** | 26 / 31 通过，1 项跳过¹⁰ |

¹⁰ 仅 poppler 的数字在模拟容器中测得：其中 4 项（中文样例的标题、无标签摘要、首段，以及中英两份 PDF 的证据定位）在该容器里 v1.2 同样失败——那里的 `pdftotext` 取不出中文样例的文字层，页面被转去 OCR；与本次改动无关。跳过的 1 项为无题注表（依赖表格横线，poppler 不提供）。

v1.0 未通过的项目包括：中英混排扫描件第 1 页选定英文后全部页面都用英文模型（中文页识别成乱码）；中文无标签摘要识别不出、正文被当成作者信息；短文档的期刊行和版权行混入正文；case.json 中的 `"output": "/app/output"` 路径键被误当成输出契约，真正的 `output_contract` 字段（`title`、`page_count` 等）没有写入；契约字段 `answers` 被内置部分覆盖。

### 3. 真实论文（arXiv 公开论文，本地运行）

“覆盖率”指与独立提取器相比的词元召回率；耗时为解析本身（不含截图）。

| 论文 | 版式 | 后端 | 页数 | 耗时 | 章节 | 表 | 图 | 参考文献 | 剔除页眉页脚等 | 跨栏/页接续 | 覆盖率 |
|---|---|---|---|---|---|---|---|---|---|---|---|
| BERT (1810.04805) | ACL 双栏 | PyMuPDF | 16 | 1.1s | 33 | 8 | 5 | 58 | 1 | 15 | 99.7% |
| ResNet (1512.03385) | CVPR 双栏 | PyMuPDF | 12 | 1.1s | 21 | 15⁵ | 7 | 52 | 21 | 13 | 99.5% |
| Transformer (1706.03762) | NeurIPS 单栏 | PyMuPDF | 15 | 1.2s | 26 | 4⁶ | 5 | 40 | 15 | 6 | 99.5% |
| LIGO PRL (1602.03837) | revtex 双栏 + 千人作者表 | PyMuPDF | 16 | 1.6s | 11 | 1 | 4 | 43⁷ | 111 | 13 | 99.2% |
| 同上 | 同上 | poppler | 16 | 0.8s | 12 | 1 | 4 | 43⁷ | 113 | 15 | 99.3% |

v1.3 在这四篇论文上的变化（逐块对比 v1.2 输出，其余块不变）：

| 论文 | v1.2 | v1.3 |
|---|---|---|
| Transformer | 第 1 页 ∗ † ‡ 作者注与 “31st Conference on NIPS” 行并入摘要（摘要 5 块）；版权许可说明被当成章节标题；作者/单位只有整块文字 | 摘要 1 块，4 条脚注；许可说明不再是章节；8 位作者各自带单位、邮箱、bbox |
| BERT | 第 3、4 页两处“两条脚注粘成一条”（`1https://… 2http://…`） | 拆成独立脚注（各带 marker 与 bbox）；4 位作者按 `{a,b,c,d}@google.com` 顺序配邮箱 |
| ResNet | Table 1 堆叠单元格行序错乱（101-layer 列 `1×1,512 / 3×3,128 / 1×1,128`），方括号显示为 `�` 或私用区字符 | 自上而下 `[ 1×1, 128 3×3, 128 1×1, 512 ] ×4`，五列全部正确，修补写入 `issues` |
| LIGO PRL | APS 符号字体乱码：`z ¼ 0.09þ0.03`、`ðm1m2Þ`、`PSR B1913þ16`，表 1 数值 `36þ5` | `z = 0.09+0.03`、`(m1m2)`、`PSR B1913+16`、`36+5`；共 62 处还原，记入 change_records |

⁵ 含第 7 页 CIFAR 网络结构那张没有题注的小表（v1.1 新识别）。⁶ 表 3 的 (A)–(D) 分组标签按 rowspan 还原为 4/2/7/4 行，两种后端结果一致。⁷ revtex 快报没有 “References” 标题，由连续的 `[n]` 条目自动定位；其余条目夹在作者单位列表中，`parse_report.md` 会提示编号不连续。

## 异常处理

| 情况 | 处理 |
|---|---|
| 加密 PDF（需用户密码） | `parse_error.json` + status=failed，说明需要密码，不猜测内容 |
| 损坏或非 PDF | 同上，kind=damaged |
| 无文字层（扫描件） | 自动 OCR（每页选择语言，中英混排文档逐页切换，可指定语言），输出每页语言、平均置信度和低置信词及坐标；无 OCR 引擎时 status=partial，列出接管页 |
| 乱码字体（`(cid:)`、私用区字符） | 该页转 OCR，并在 `pages[].bad_chars` 中计数 |
| 符号字体缺 ToUnicode（APS / Elsevier 的 Advent `AdvP…` 字体：`z ¼ 0.09þ0.03`） | 按字体名把 `þ ¼ ð Þ ½ Š` 还原为 `+ = ( ) [ ]`，次数与字体写入 `change_records` 和 `parse_report.md`；只对这类符号字体生效，正文字体中的同形字母不动 |
| 契约要求翻译 / 总结 / 知识结构等 Agent 自己产出的字段 | 这类字段（`translated_*`、`summary`、`knowledge_*`、`comparison` …）从不用原文自动填充，标为 `UNFILLED`，SKILL.md 给出按块对应原文、保留编号与数值的写法 |
| 表格是图片 | 有 tesseract 时对题注旁的图片做 OCR 并还原单元格（`body_source: ocr-tesseract`，记入 `uncertainties` 提示逐格核对）；读不出时记为 `image_only`，写入 `not_reported`，提供题注+图片截图 |
| 公式无文字层或被合并 | 公式编号连续性检查列出缺失的编号 |
| 解析器异常 | 单个文件失败不影响其他文件；SKILL.md 给出 `--backend poppler` 和 `pdftotext -layout` 两级兜底 |
| 预算紧张 | run_case 先写出草稿 result.json，此后每一步都只做增量改进 |
| 契约字段类型不符 / 缺文件 | run_case 列出每个契约字段的来源与类型；finalize 输出 `CHECK: TYPE ...`、`CHECK: CONTRACT FILE missing ...`，Agent 修正后重跑 |
| 一题多份材料 | 只解析 case.json 指定的材料，其余输入文件列为 OTHER（可用 `--material` 加入）；`query.py` 默认查询本次运行的全部文档，`--doc` 选一份；各文档块编号相同（都从 `p1-b0` 起），证据按 `file` 定位，未写 `file` 时取引文实际所在的文档，仍无法区分时标出 `ambiguous_files` 并写入 uncertainties |
| 契约禁止额外字段（`additionalProperties: false`） | `result.json` 只保留契约字段与 `case_id`、`status`、`status_reason`，其余内置部分写入 `result_details.json` |

## 自查方法

1. 看 `parse_report.md` 中的 `[CHECK]` 行，以及覆盖率是否 ≥ 97%。
2. `query.py page N` 对照 `previews/page-NNN.png` 检查阅读顺序。
3. 对照 `crops/table-N.png` 检查 `tables/table-N.csv`。
4. 运行 `finalize.py`，确认引文全部 verified、没有空的契约字段、没有类型问题；`REVIEW` 行列出自动填充的字段，逐个确认是否符合题意。
5. 修改解析器后：`python3 bench/make_hard_cases.py out_hard && python3 bench/selftest.py out_hard`（31 项），以及 `bench/score_bench.py` 的标准样例；改动 SKILL.md 或契约处理后，用 `bench/agent_sim/` 做 Agent 端到端对照。

## 局限

- 公式按文字层线性化输出（保留 Unicode 符号和上下标），不重建 LaTeX；左对齐（`fleqn`）且不带编号的行间公式不单独识别，会留在段落中（试过按“整行几乎全是数学字体”判断，会把段落末尾的行内公式行误切出来，已放弃）；需要精确符号时请看公式截图（`crops/eq-N.png`；纯文本模型无法看图时，以文字层为准并在 uncertainties 中说明）。
- 无横线、对齐很弱的表格列划分可能出错（会在 `issues` 中提示）。单元格内换行只在有明确续写信号时合并（小写开头、连字符/介词结尾、中文填满列宽），以大写开头续写的英文单元格不会合并。跨行标签只在能确定归属时还原（分组横线、对称居中、顶对齐）。无题注表格的识别依赖表格横线，poppler 后端无此信息。
- 图片表格的 OCR 结果按单元格给出，但数值可能有个别识别错误，结果中已标明来源与平均置信度，需对照截图核对。
- 没有粗体信息的后端（poppler、OCR）对不加粗、不编号的标题识别偏弱。
- 竖排中文、三栏以上的复杂杂志版式、旋转的横向表格只做基础处理。
- OCR 精度取决于评测镜像中 tesseract 的语言包。中文扫描件的字错率约 4–6%（tesseract `chi_sim`），扫描件中的表格单元格和公式编号识别不稳定（见上表）。本地扫描件测试只用了干净的渲染图，真实扫描（倾斜、噪点、低分辨率）效果会更差；`parse_report.md` 会列出每页平均置信度和低置信词，供人工复核。

## 复用

- 作为库调用：`from pdfdoc import structure, export`，然后 `doc = structure.parse_document(pdf)`、`export.write_outputs(doc, pdf, outdir)`。
- `document.json` 可直接作为翻译、问答、知识图谱等下游任务的输入：按块定位，按节切分，按 `continues` 拼接段落。
- `bench/` 可复用于评测任何 PDF 提取方案：替换生成器中的文本，即可构造新学科的样本；`make_hard_cases.py` 的每个难例都由同一份 Python 数据同时生成 LaTeX 和标准答案。

## 维护信息

- 版本：1.3.0（2026-11-28）。许可：MIT。仓库：https://github.com/xhxmt/bohrium/tree/main/reading01 ，问题反馈请在该仓库提交 issue。
- 1.3.0 更新（依据作者自查与 Gemini 评审）：作者—单位—邮箱结构化（作者网格按列、上标标记、唯一单位三种关联规则，带 bbox 与依据，拿不准的不关联）；摘要下方的作者注 / 会议行归为脚注；堆叠脚注拆分；无标签摘要只在首个章节标题之前推断；标题上方的许可说明不再算章节；中文加粗句不再算标题；APS / Elsevier Advent 符号字体乱码还原；表格中 `82.3±0.4`、`80.6/80.1`、`84.1(0.2)` 计为数值（跨行分组标签可还原，分列规则不变）；大括号撑高的单元格按行自上而下读、括号还原为 `[ ]`；契约中翻译 / 总结 / 知识结构类字段不再被原文自动填充，新增 `metadata`、`authors`、`affiliations`、`emails`、`ocr_quality`、`text_blocks` 自动映射，`sections` 带 bbox，超长表格标注截断；引文核实成功但块号写错时纠正定位（保留 `cited_block`），表格引文可按展开后的行或跨页合并行核对，`document.md` 中的 Markdown 转义不再影响核对；`run_case.py` 显示 case.json 的全部其他键（约束、语言、证据范围），面向 Skill 作者的通用 task.md 只显示开头；`query.py overview` 一次看全貌，`table N` 对跨页续表直接给出合并表；finalize 有问题时醒目提示；SKILL.md 增加批量查询、按任务类型的做法、交付前自检和“不要重建解析结果”。新增 `bench/agent_sim/`（Agent 端到端对照模拟器）、难例 `front_matter` 与 9 项自检。本地模拟（DeepSeek，3 案例 × 3 次）：全部检查通过、9/9 success，A 案例平均请求 46.0 → 29.3。
- 1.2.0 更新：tesseract 改为单线程运行（`OMP_THREAD_LIMIT=1`）——在限核容器或多任务并发时，OpenMP 线程空转使 3 页扫描件从 12 秒拖到约 480 秒；`run_case.py` 在解析前先写出符合契约的占位 `result.json`；OCR 页面上带编号的短标题（如“1 Introduction”，无粗体信息、无下伸笔画时字号被低估）不再漏识别；新增 `query.py ocr 页码 --bbox x0,y0,x1,y1` 局部重识别（坐标同解析结果，单线程、带超时）；SKILL.md 说明任务以 case.json 为准、不要直接调用 tesseract。本地模拟评测（DeepSeek 模型、64 次请求 / 900 秒预算、6 路并发）中扫描件案例自检通过率 78% → 100%，平均用时 900 秒超时 → 226 秒。
- 1.1.0 更新：契约字段不再被内置部分覆盖、按声明类型填充与校验、检查契约要求的文件；引文核对支持跨栏续接、省略号与宽松匹配，`table-N` 等对象 id 直接解析；`query.py` 多文档查询、括号等字符按原文搜索；表格：跨行、单元格换行、无题注表、跨页续表拼接、图片表格 OCR、表头不再并入题注、长表与超栏宽表；中文按字数判断段落长度（无标签摘要）；OCR 逐页选择语言；短文档的一次性页眉页脚；居中作者行不再阻断分栏；渲染复用已打开的 PDF；独立对照提取按连续页段进行；移除未使用的联网上传代码；修正英文样例生成器；新增难例生成器与自检脚本。第二轮（依据 Gemini 复评）：扁平契约中的 `title`/`notes`/`description` 保留为字段；契约中出现的输出文件名不再被当作缺失的输入材料；任务文字只从契约以外读取、截断上限提高到 16000 字并明确提示；多文档证据按文件定位；契约禁止额外字段时另写 `result_details.json`；`answers.json` 结构不对时报 CHECK；紧凑表格（列距小）不再误判为图片表格；附录表号（`Table A1`、`附表1`）与无标点题注；表头分界优先取全宽横线；PyMuPDF 下文字被转成曲线的页面触发 OCR；中文多行标题、题注、参考文献拼接不再插入空格；OCR 试跑语言的校正记录不再混入；`query.py` 输出上限；SKILL.md 命令改为完整路径，并补充纯文本模型与大字段的写法。
- 目录：`SKILL.md`（Agent 入口）、`scripts/`（run_case / query / finalize / parse_pdf，以及 `pdfdoc/` 库）、`docs/`、`examples/`（示意）、`bench/`（标准样例、难例、打分与自检脚本）、`bench/agent_sim/`（基础 Agent / Skill Agent 端到端对照模拟器）。
