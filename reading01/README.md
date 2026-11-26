# scientific-pdf-parser · 科研 PDF 版面解析 Skill（阅读01｜文献文档解析）

把一篇科研论文 PDF 解析成**可直接继续处理、且每一处都能指回原文**的结构化结果：

- **排得对**：自动判断单栏/双栏/三栏，通栏标题、通栏图表把页面切成若干带，带内逐栏阅读；跨栏、跨页的段落自动接续并记录接续关系。
- **提得全**：页眉、页脚、页码、页边竖排水印、审稿行号从正文流中剔除，但保留在 `furniture` 列表中可查。断行连字符按“全文是否出现过带连字符的写法”决定保留还是去掉，每一处都有记录。连字（ﬁ/ﬂ）会展开。
- **拆得清**：章节层级（1 / 1.1 / A.1 / I. / 一、/ 第X章）；表格按单元格还原，支持多层表头、跨列与分组行，输出 CSV/HTML/Markdown；带编号的行间公式；上下标（`<sup>/<sub>`）；图表题注；脚注；参考文献逐条切分（支持编号式与悬挂缩进式）。
- **核得回**：每个区块都带 `页码 + bbox`。`document.md` 每段前有定位锚点。表格、公式、图有截图，每页有阅读顺序标注图。答案中的引文会被逐字核对，并定位到具体的块或表格行。
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
| MinerU API（仅当用户自行设置 `MINERU_TOKEN`） | 可选的云端解析接口（`backends.mineru_markdown`） | 默认不使用；本 Skill 不附带任何密钥，评测中不会调用 |

测试环境：Python 3.13，PyMuPDF 1.26（MuPDF 1.28），pdfplumber 0.11，poppler 25.03。代码按 Python 3.7 语法编写，已用 vermin 检查。

## 输出

`/app/output/`
- `result.json`：任务结果。包含 `case_id`、`status`（success/partial/cannot_determine/refused/failed）、`status_reason`、**全部 output_contract 字段**（能自动填的由脚本填，其余由 Agent 补充）、`answers`（每条附证据与引文核对结果）、`materials`、`structure_summary`、`uncertainties`、`not_reported`、`manual_takeover`、`change_records`、`contract`（哪些字段是自动填的、哪些由 Agent 填写、哪些仍为空）、`run`。
- `result_summary.md`：人可读摘要。
- `parsed/document.md`：按阅读顺序排列的全文。每块前有 `<!-- p3-b12 p3 [x0,y0,x1,y1] paragraph -->` 锚点；跨栏、跨页段落合并输出并注明 `continues across A -> B`；表格内嵌 Markdown；公式带编号和截图链接。
- `parsed/document.json`：完整结构（字段见 `docs/OUTPUT_SCHEMA.md`）。
- `parsed/parse_report.md`：自检报告。
- `parsed/tables/table-N.{csv,html,md}`、`parsed/crops/{table,figure,eq}-N.png`、`parsed/previews/page-NNN.png`（彩框为区块，数字为阅读顺序，灰框为剔除的页眉页脚）。

坐标约定：PDF 点（1/72 英寸），原点在页面左上角；块 id `p{页}-b{序号}`，序号即本页阅读顺序。

## 方法（流水线）

1. **取词**：按 PyMuPDF → pdfplumber → poppler → pypdf 的顺序选择后端，统一成“带坐标、字号、字体、粗体的词”。逐页检查文字层（字符数、`(cid:..)`/私用区/替换符比例），无文字层或乱码页转 OCR。
2. **行内修补**：把被 PDF 拆成独立行的堆叠上下标（如 410<sup>+160</sup><sub>−180</sub>）挂回宿主行；行内按大间隙切成片段。
3. **页面杂项**：跨页重复且位置相近的页眉页脚（数字归一化后比较）、页码、页边竖排文字、审稿行号。
4. **分栏**：用窄行的 x 覆盖直方图找栏缝。要求正文以窄行为主、栏缝两侧都有足够的长行，并参考整篇文档的多数结果。对于左右栏被提取器粘在一起的中文行，在栏缝处切开。
5. **浮动体**：识别题注（排除正文中位于行首的 “Table 3 shows”），在题注两侧寻找表格行。以其他题注、正文行、表格横线群为边界，有歧义时按本页/本文的题注位置惯例分配。单元格用“多数行留空的 x 区间”定列，跨列单元格记为 colspan，按横线或数值行判断表头行数。图的区域用图片框、矢量图聚类，或“题注上方的非正文行”确定，图内文字单独存放。
6. **阅读顺序**：通栏元素把页面切成带，带内按栏从左到右、每栏自上而下。
7. **成块与分类**：同一行的片段合并；依据行距、字号变化、首行缩进、句末标点、居中的数学行切分段落。分类出标题、作者单位、摘要（含无标签摘要的推断）、章节标题（编号模式、粗体、字号排序）、列表、公式、脚注、参考文献。跨栏/跨页接续的条件：前段未以句末标点结束，后段以小写字母或中文开头且不缩进。
8. **输出与自检**：见上文。

## 本地对照评测（可复现，非平台成绩）

`bench/make_bench.py` 用 xelatex 生成中英文两份双栏测试论文（页眉页脚、跨栏跨页段落、多层表头表格、编号公式、脚注、参考文献），并同时生成精确的标准答案。`bench/score_bench.py` 按标准答案打分：

| 测试文档 | 方法 | 段落完整 | 段落顺序 | 页眉页脚混入 | 标题层级 | 表格单元格 | 公式编号 |
|---|---|---|---|---|---|---|---|
| 中文双栏 | `pdftotext` | 24% | 90% | 3 | — | 0%¹ | — |
| 中文双栏 | `pdftotext -layout` | 0% | 90% | 3 | — | 100%¹ | — |
| 中文双栏 | PyMuPDF `get_text()` | 95% | 100% | 3 | — | 0%¹ | — |
| 中文双栏 | **本 Skill（PyMuPDF 后端）** | **100%** | **100%** | **0** | **100%** | **100%** | **100%** |
| 中文双栏 | 本 Skill（仅 poppler） | 90% | 100% | 0 | 100% | 100% | 100% |
| 英文双栏 | `pdftotext` | 71% | 90% | 6 | — | 0%¹ | — |
| 英文双栏 | PyMuPDF `get_text()` | 81% | 100% | 6 | — | 0%¹ | — |
| 英文双栏 | **本 Skill（PyMuPDF 后端）** | **100%** | **100%** | **0** | **100%** | **100%** | **100%** |
| 英文双栏 | 本 Skill（仅 poppler） | 95% | 100% | 0 | 100% | 100% | 100% |
| 英文双栏（栅格化扫描件²） | 本 Skill，OCR 路径 | 100% | 100% | 0 | 100% | 100% | 100% |

¹ 纯文本没有单元格结构，这里按“一行内按顺序出现整行数值”宽松计分。² 用于验证渲染→预处理→TSV→版面分析整条 OCR 链路；识别文字由测试替身提供，不代表 tesseract 的识别精度。

真实论文（arXiv 公开论文，本地运行；“覆盖率”指与独立提取器相比的词元召回率）：

| 论文 | 版式 | 后端 | 页数 | 耗时 | 章节 | 表 | 图 | 参考文献 | 剔除页眉页脚等 | 跨栏/页接续 | 覆盖率 |
|---|---|---|---|---|---|---|---|---|---|---|---|
| BERT (1810.04805) | ACL 双栏 | PyMuPDF | 16 | 0.9s | 33 | 8 | 5 | 58 | 1 | 15 | 99.7% |
| ResNet (1512.03385) | CVPR 双栏 | PyMuPDF | 12 | 0.8s | 21 | 14 | 7 | 52 | 21 | 13 | 99.5% |
| Transformer (1706.03762) | NeurIPS 单栏 | PyMuPDF | 15 | 0.7s | 26 | 4 | 5 | 40 | 15 | 6 | 99.5% |
| LIGO PRL (1602.03837) | revtex 双栏 + 千人作者表 | PyMuPDF | 16 | 1.2s | 11 | 1 | 4 | 43³ | 111 | 15 | 99.2% |
| 同上 | 同上 | poppler | 16 | 0.6s | 12 | 1 | 4 | 43³ | 113 | 18 | 99.9% |

³ revtex 快报没有 “References” 标题，由连续的 `[n]` 条目自动定位；其余条目夹在作者单位列表中，`parse_report.md` 会提示编号不连续。

## 异常处理

| 情况 | 处理 |
|---|---|
| 加密 PDF（需用户密码） | `parse_error.json` + status=failed，说明需要密码，不猜测内容 |
| 损坏或非 PDF | 同上，kind=damaged |
| 无文字层（扫描件） | 自动 OCR（中英文自动试探，可指定语言），输出每页平均置信度和低置信词及坐标；无 OCR 引擎时 status=partial，列出接管页 |
| 乱码字体（`(cid:)`、私用区字符） | 该页转 OCR，并在 `pages[].bad_chars` 中计数 |
| 表格是图片 | 表格记为 `image_only`，写入 `not_reported`，提供题注位置和截图 |
| 公式无文字层或被合并 | 公式编号连续性检查列出缺失的编号 |
| 解析器异常 | 单个文件失败不影响其他文件；SKILL.md 给出 `--backend poppler` 和 `pdftotext -layout` 两级兜底 |
| 预算紧张 | run_case 先写出草稿 result.json，此后每一步都只做增量改进 |

## 自查方法

1. 看 `parse_report.md` 中的 `[CHECK]` 行，以及覆盖率是否 ≥ 97%。
2. `query.py page N` 对照 `previews/page-NNN.png` 检查阅读顺序。
3. 对照 `crops/table-N.png` 检查 `tables/table-N.csv`。
4. 运行 `finalize.py`，确认引文全部 verified、没有空的契约字段。

## 局限

- 公式按文字层线性化输出（保留 Unicode 符号和上下标），不重建 LaTeX；需要精确符号时请看公式截图。
- 无横线、对齐很弱的表格，以及单元格内换行的表格，列划分可能出错（会在 `issues` 中提示）；跨页续表按相同表号标记 `continued_from`，不自动拼接。
- 没有粗体信息的后端（poppler、OCR）对不加粗、不编号的标题识别偏弱。
- 竖排中文、三栏以上的复杂杂志版式、旋转的横向表格只做基础处理。
- OCR 精度取决于评测镜像中 tesseract 的语言包。

## 复用

- 作为库调用：`from pdfdoc import structure, export`，然后 `doc = structure.parse_document(pdf)`、`export.write_outputs(doc, pdf, outdir)`。
- `document.json` 可直接作为翻译、问答、知识图谱等下游任务的输入：按块定位，按节切分，按 `continues` 拼接段落。
- `bench/` 可复用于评测任何 PDF 提取方案：替换生成器中的文本，即可构造新学科的样本。

## 维护信息

- 版本：1.0.0（2026-11）。许可：MIT。仓库：https://github.com/xhxmt/bohrium/tree/main/reading01 ，问题反馈请在该仓库提交 issue。
- 目录：`SKILL.md`（Agent 入口）、`scripts/`（run_case / query / finalize / parse_pdf，以及 `pdfdoc/` 库）、`docs/`、`examples/`（示意）、`bench/`。
