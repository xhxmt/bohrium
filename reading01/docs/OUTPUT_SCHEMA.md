# 输出字段说明（document.json / result.json）

坐标：PDF 点（pt，1/72 英寸），原点在页面**左上角**，`bbox = [x0, y0, x1, y1]`。
块 id：`p{页码}-b{本页阅读序号}`；对象 id：`table-N`、`figure-N`、`eq-N`、`ref-N`、`sec-N`。

## document.json

| 字段 | 内容 |
|---|---|
| `source` | `file`、`sha256`、`bytes`、`kind`（pdf/image）、`pages_total`、`pages_parsed` |
| `engine` | `backend`（pymupdf/pdfplumber/poppler/pypdf/ocr）、`tools`（探测到的引擎及版本）、`body_font_size`、`elapsed_s` |
| `pages[]` | `page`、`width`、`height`、`status`（text/ocr/empty/no_text_layer[_unreadable]/garbled_text_layer）、`text_chars`、`bad_chars`、`image_cover`、`columns`、`gutters`、`column_source`（page/document/single）、`source`、`notes` |
| `metadata` | `title`、`title_block`、`front_matter[]`（作者/单位块）、`abstract`、`abstract_blocks`、`abstract_inferred`、`keywords` |
| `sections[]` | `id`、`level`、`number`、`title`、`heading_text`、`page`、`block`、`bbox`、`parent` |
| `blocks[]` | `id`、`page`、`type`、`order`（全局阅读顺序）、`bbox`、`col`（栏号，-1 表示通栏）、`section`、`text`、`text_rich`（含 `<sup>/<sub>`）、`size`、`bold`、`lines[]`（逐行 `text`+`bbox`）；可选字段：`level`、`number`、`continues`/`continued_by`（跨栏/跨页接续）、`lead`（段首加粗小标题）、`eq_numbers`、`inferred`、`ocr_conf`、`ref`（表/图块指向的对象） |
| `tables[]` | `id`、`label`、`number`、`page`、`block`、`bbox`、`caption{text,bbox,position}`、`nrows`、`ncols`、`header_rows`、`header[]`（多层表头合并后的列名，以 “ / ” 连接）、`matrix[][]`、`grid[]`（逐行：`cells[]{text,rich,col,colspan,bbox}`、`header`、`bbox`）、`markdown`、`html`、`csv`、`crop`、`issues[]`、`image_only`、`ruling_lines_used`、`continued_from` |
| `figures[]` | `id`、`kind`、`label`、`number`、`page`、`block`、`bbox`（图形区域）、`caption`、`text_inside[]`（图内文字）、`crop`、`notes` |
| `equations[]` | `id`、`number`（如 “(3)”）、`page`、`block`、`bbox`、`text`、`text_rich`、`crop`、`note` |
| `footnotes[]` | `id`、`page`、`bbox`、`text` |
| `references[]` | `id`、`label`（[n] 或序号）、`text`、`page`、`pages`、`block`、`bbox`、`year`、`doi`、`arxiv`、`split_rule` |
| `furniture[]` | 从正文流剔除的页面杂项：`page`、`role`（running_header/running_footer/page_number/margin_text/line_number）、`text`、`bbox` |
| `changes` | `dehyphenation[]`（`from` “pre-|training”，`to`，`rule`）、`ligatures_expanded`、`invisible_chars_removed`、`merged_paragraphs[]`、`lines_split_at_column_gutter`、`ocr_numeric_corrections[]`（OCR 数字校正与中文语境半角→全角标点还原，`[原词, 改后]`）、`furniture_removed` |
| `ocr` | `engine`、`lang`、`pages[]{page,lang,mean_conf,words,low_conf_words[[词,置信度,bbox]],preprocess}`、`skipped_pages`、`notes` |
| `qa` | `status`（success/success_with_warnings/partial）、`checks[]{check,value,ok,…}`、`token_recall_vs_<提取器>`、`failed_checks` |
| `warnings[]`、`previews[]` | 警告；页面预览图的相对路径 |

## result.json

| 字段 | 内容 |
|---|---|
| `case_id`、`status`、`status_reason` | status 取值：success / partial / cannot_determine / refused / failed |
| *output_contract 中的每个字段* | 原样保留字段名，按字段名自动填充（标题、章节、表格、图、公式、参考文献、脚注、页面杂项、阅读顺序、证据、不确定项、修改记录等），Agent 可在 `answers.json.fields` 中覆盖 |
| `answers[]` | `question`、`answer`、`confidence`、`evidence[]{block, quote, file, page, bbox, block_type, quote_check{verified, how, page, block, bbox}}` |
| `materials[]` | 每个输入文件的 sha256、页数、后端、产物路径、QA 状态、与独立提取器相比的覆盖率 |
| `structure_summary[]` | 标题、各类结构计数、每页栏数 |
| `uncertainties[]` | 解析警告、未通过的 QA 检查、OCR 低置信词、未核实的引文，以及 Agent 补充的不确定项 |
| `not_reported[]` | 原文未报告或无法读取的项目（如图片表格、缺失文件、远程链接），附人工接管建议 |
| `manual_takeover[]` | 需要人工处理的页或文件及原因 |
| `change_records[]` | 相对原始文字层所做的全部改动（剔除的页面杂项、去连字符、段落接续、连字展开、OCR 数字与标点校正），原始逐行文字保留在 `document.json` 中 |
| `contract` | 契约原文；`auto_filled`、`agent_filled`、`unfilled` 三类字段清单 |
| `run` | 是否为草稿、完成时间、case.json 路径 |
