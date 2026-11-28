# 输出字段说明（document.json / result.json）

坐标：PDF 点（pt，1/72 英寸），原点在页面**左上角**，`bbox = [x0, y0, x1, y1]`。
块 id：`p{页码}-b{本页阅读序号}`；对象 id：`table-N`（跨页续表的后续部分为 `table-N-part2`…，无题注表为 `table-uK`）、`figure-N`、`algorithm-N`、`eq-N`、`ref-N`、`sec-N`。证据中的 `block` 填块 id 或对象 id 均可。

## document.json

| 字段 | 内容 |
|---|---|
| `source` | `file`、`sha256`、`bytes`、`kind`（pdf/image）、`pages_total`、`pages_parsed` |
| `engine` | `backend`（pymupdf/pdfplumber/poppler/pypdf/ocr）、`tools`（探测到的引擎及版本）、`body_font_size`、`elapsed_s` |
| `pages[]` | `page`、`width`、`height`、`status`（text/ocr/empty/no_text_layer[_unreadable]/garbled_text_layer）、`text_chars`、`bad_chars`、`image_cover`、`columns`、`gutters`、`column_source`（page/document/single）、`source`、`notes` |
| `metadata` | `title`、`title_block`、`front_matter[]`（作者/单位块）、`authors[]{name, markers[], affiliation, affiliation_rule, email, email_rule?, page, block, bbox}`、`affiliations[]{text, marker, page, block, bbox}`、`emails[]{email, shared, page, block, bbox}`、`abstract`、`abstract_blocks`、`abstract_inferred`、`keywords`。作者与单位只在版面能确定时关联：上标标记一致（`superscript marker`）、作者网格同一列的下一行（`same column, next row`）、全文只有一个单位（`single affiliation for all authors`）；否则 `affiliation` 为 null。`{a,b}@domain` 形式的邮箱按作者顺序展开（`brace list, in author order`） |
| `sections[]` | `id`、`level`、`number`、`title`、`heading_text`、`page`、`block`、`bbox`、`parent` |
| `blocks[]` | `id`、`page`、`type`、`order`（全局阅读顺序）、`bbox`、`col`（栏号，-1 表示通栏）、`section`、`text`、`text_rich`（含 `<sup>/<sub>`）、`size`、`bold`、`lines[]`（逐行 `text`+`bbox`）；可选字段：`level`、`number`、`continues`/`continued_by`（跨栏/跨页接续）、`lead`（段首加粗小标题）、`eq_numbers`、`inferred`、`ocr_conf`、`ref`（表/图块指向的对象） |
| `tables[]` | `id`、`label`、`number`（无题注表为 null）、`page`、`block`、`bbox`、`caption{text,bbox,position}`、`nrows`、`ncols`、`header_rows`、`header[]`（多层表头合并后的列名，以 “ / ” 连接）、`matrix[][]`（跨行标签在其覆盖的每一行重复）、`grid[]`（逐行：`cells[]{text,rich,col,colspan,rowspan?,bbox}`、`header`、`bbox`）、`markdown`、`html`（含 `colspan`/`rowspan`）、`csv`、`crop`、`issues[]`（含 `repaired: …` 修补记录）、`image_only`、`ruling_lines_used`；可选：`uncaptioned`、`continued_from`、`continued_in[]`、`merged{parts[],nrows,ncols,header_rows,matrix,markdown,notes,csv}`（跨页续表拼接结果，写在第一部分上）、`body_source`（`ocr-tesseract:<语言>`：表体由图片 OCR 读出）、`ocr_mean_conf`、`image_bbox`（表格图片位置） |
| `figures[]` | `id`、`kind`、`label`、`number`、`page`、`block`、`bbox`（图形区域）、`caption`、`text_inside[]`（图内文字）、`crop`、`notes` |
| `equations[]` | `id`、`number`（如 “(3)”）、`page`、`block`、`bbox`、`text`、`text_rich`、`crop`、`note` |
| `footnotes[]` | `id`、`block`、`page`、`bbox`、`text`、`marker`（`1`、`∗`、`†` …）。一个块里上下堆叠的多条脚注按行首上标拆成多条，`id` 为 `块id#n`。第 1 页摘要下方字号更小的作者注（∗ † ‡）与会议/预印本说明行归为脚注，不并入摘要 |
| `references[]` | `id`、`label`（[n] 或序号）、`text`、`page`、`pages`、`block`、`bbox`、`year`、`doi`、`arxiv`、`split_rule` |
| `furniture[]` | 从正文流剔除的页面杂项：`page`、`role`（running_header/running_footer/page_number/margin_text/line_number）、`text`、`bbox`；`rule`（不靠重复、按“页边小字 + 期刊/DOI/版权关键词”剔除时注明） |
| `changes` | `dehyphenation[]`（`from` “pre-|training”，`to`，`rule`）、`ligatures_expanded`、`invisible_chars_removed`、`merged_paragraphs[]`、`lines_split_at_column_gutter`、`ocr_numeric_corrections[]`（OCR 数字校正与中文语境半角→全角标点还原，`[原词, 改后]`）、`furniture_removed`、`table_repairs[]{table,page,repair}`（单元格换行合并、rowspan 还原）、`pi_font_glyphs_remapped` / `pi_fonts`（APS / Elsevier 等 Advent 排版系统的符号字体没有 ToUnicode 映射，`þ ¼ ð Þ ½ Š` 还原为 `+ = ( ) [ ]` 的次数与字体名） |
| `ocr` | `engine`、`lang`（全部页相同时为该语言，否则为 “per page: …”）、`pages[]{page,lang,mean_conf,words,low_conf_words[[词,置信度,bbox]],preprocess}`、`skipped_pages`、`notes` |
| `qa` | `status`（success/success_with_warnings/partial）、`checks[]{check,value,ok,…}`、`token_recall_vs_<提取器>`、`failed_checks` |
| `warnings[]`、`previews[]` | 警告；页面预览图的相对路径 |

## result.json

字段顺序：`case_id`、`status`、`status_reason`，然后是 output_contract 的全部字段，最后是内置部分。

**契约字段的取值优先级**：Agent 在 `answers.json` 的 `fields` 中给出的值 > 同名的内置部分（如 `answers`、`uncertainties`、`sources`）> 按字段名从解析结果自动填充。字段名按整词匹配（`page_count` = page + count → 页数，`title` → 标题字符串，`n_tables` → 表格数，`full_text` / `markdown` → 阅读顺序全文，说明中写明文件名或“路径”的字段 → 文件名），不按子串匹配；含义不明确的字段留空，由 Agent 填写。**要求 Agent 自己产出内容的字段**（字段名含 translat / summar / analy / compar / knowledge / entit / relation / claim / verif / conflict / explan / interpret / contribution，或 zh / en / chinese / english / graph 等）一律不自动填充，标为 `UNFILLED`，避免把原文当成译文、总结或知识结构。`metadata` → 标题/作者/单位/邮箱/摘要及各自定位；`authors` / `affiliations` / `emails` → 结构化列表；`ocr_quality` → 每页 OCR 语言、平均置信度与低置信词；`text_blocks` → 阅读顺序的全部文字块（id、页、bbox、类型、文本）；表格超过 150 行时 `rows` 截断并给出 `rows_truncated{shown,total,full_table}`。契约声明了类型（JSON Schema 的 `type` / `items`，或 “string”“list of …”“object” 之类的描述）时，自动值按类型转换（如对象列表 → 字符串列表），无法安全转换时留空并在 `contract.fill_report` 中说明。

| 字段 | 内容 |
|---|---|
| `case_id`、`status`、`status_reason` | status 取值：success / partial / cannot_determine / refused / failed；必填契约字段仍为空时 success 自动降为 partial |
| *output_contract 中的每个字段* | 原样保留字段名，取值见上 |
| `answers[]` | `question`、`answer`、`confidence`、`evidence[]{block, quote, file, page, bbox, block_type, object?, ambiguous_files?, quote_check{verified, match(exact/loose/ellipsis), how, file, page, block, bbox, object?}}`；`block` 可写对象 id（`table-2`），会解析为所在块并保留 `object`；引文核实成功但不在所引的块里时，定位改为实际所在的块，原值存入 `cited_block` 并标 `locator_corrected: true`；表格引文可以按 `query.py table` / CSV 的行（跨行标签已重复）或合并后的跨页续表行书写；多份 PDF 时 `file` 指定文档（未给时取引文所在文档，仍无法区分时列出 `ambiguous_files`） |
| `sources` | `materials[]{file, path, sha256, pages_total, pages_parsed}`、`evidence[]`（全部答案证据的扁平列表：file/page/block/object/bbox/quote/verified/for）、`locator_convention` |
| `uncertainties[]` | 解析警告、未通过的 QA 检查、OCR 低置信词、OCR 读出的图片表格、未核实的引文、finalize 发现的问题，以及 Agent 补充的不确定项 |
| `not_reported[]` | 原文未报告或无法读取的项目（如读不出的图片表格、缺失文件、远程链接），附人工接管建议 |
| `change_records[]` | 相对原始文字层所做的全部改动（剔除的页面杂项、去连字符、段落接续、连字展开、OCR 数字与标点校正、表格修补），原始逐行文字保留在 `document.json` 中 |
| `manual_takeover[]` | 需要人工处理的页或文件及原因 |
| `materials[]` | 每个输入文件的 sha256、页数、后端、产物路径、QA 状态、与独立提取器相比的覆盖率 |
| `structure_summary[]` | 标题、各类结构计数、每页栏数 |
| `contract` | `source`（契约原文）、`fields`、`soft_fields`（case.json 无契约时从任务文字中提到的字段）、`files`（契约要求生成的文件）、`auto_filled`、`agent_filled`、`unfilled`、`type_problems[]`、`fill_report{字段: {source, type_expected, type_got, coerced_from?, locators?, note?}}`。契约本身有名为 `contract` 的字段时，此部分改名为 `skill_contract_report` |
| `run` | 是否为草稿、完成时间、case.json 路径（契约有 `run` 字段时改名为 `skill_run`） |
| `skill_details` | 仅在 Agent 填写的契约字段与内置部分同名且取值不同时出现：内置部分的原始内容（如逐条核对过引文的 `answers`），保证不丢失信息 |

契约是 JSON Schema 且 `additionalProperties: false` 时，`result.json` 只含契约字段与 `case_id`、`status`、`status_reason`，上表中的其余部分写入同目录的 `result_details.json`。
