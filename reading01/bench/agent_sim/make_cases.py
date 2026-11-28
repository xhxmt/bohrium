#!/usr/bin/env python3
"""Build 3 mock cases shaped like the evaluator's /input (case.json + task.md + PDF).

These are NOT the official cases (those are hidden). They only exercise the same
runtime contract so we can see whether a DeepSeek agent finishes inside budget.
Run inside the reading01-sim container (needs PyMuPDF for the scanned case):
  docker run --rm -v $PWD:/sim -v $CORPUS:/corpus:ro reading01-sim python3 /sim/make_cases.py
"""
import json, os, shutil, sys

import fitz  # PyMuPDF

SIM = os.path.dirname(os.path.abspath(__file__))
CORPUS = sys.argv[1] if len(sys.argv) > 1 else "/corpus"
TASK_MD = sys.argv[2] if len(sys.argv) > 2 else "/task.md"
OUT = os.path.join(SIM, "cases")

CASES = [
    {
        "case_id": "sim-a-attention-structure",
        "src": "1706.03762.pdf", "pdf": "paper.pdf",
        "task": ("将这篇双栏论文解析为结构化结果：标题、作者与单位、摘要、章节层级（含编号）、"
                 "全部表格（行列与单元格）、带编号的公式、图表题注、脚注与参考文献数量。"
                 "每个结构元素必须给出页码与区块位置；无法解析的部分如实标注。"),
        "output_contract": {
            "fields": ["case_id", "status", "metadata", "sections", "tables", "equations",
                       "figures", "footnotes", "references", "evidence", "uncertainties"],
            "files": ["document.md"],
        },
    },
    {
        "case_id": "sim-b-resnet-tables-qa",
        "src": "1512.03385.pdf", "pdf": "resnet.pdf",
        "task": ("回答下列问题并给出原文依据（页码、表格/段落位置、逐字引文）：\n"
                 "Q1. Table 1 中 152-layer 网络 conv3_x 阶段的 block 结构与重复次数是什么？\n"
                 "Q2. Table 3（ImageNet validation，10-crop testing）中 ResNet-152 的 top-1 与 top-5 error 是多少？\n"
                 "Q3. 在 CIFAR-10 上，110 层 ResNet 的 error 是多少？\n"
                 "Q4. 论文报告的训练所用 GPU 型号是什么？\n"
                 "并将 Table 3 和 Table 4 输出为结构化表格。原文未报告的内容必须写明未报告。"),
        "output_contract": {
            "fields": ["case_id", "status", "answers", "tables", "evidence", "not_reported", "uncertainties"],
        },
    },
    {
        "case_id": "sim-c-bert-scanned",
        "src": "1810.04805.pdf", "pdf": "bert_scan.pdf", "scan_pages": 3, "dpi": 150,
        "task": ("这是一份扫描版（无文字层）论文的前 3 页。请识别全文文字，按阅读顺序输出，"
                 "给出标题、作者、摘要全文与章节标题；标注 OCR 置信度低或无法确认的位置。"
                 "并回答：BERT 在 GLUE 上的分数是多少，相对提升多少个绝对点？给出页码和位置。"),
        "output_contract": {
            "fields": ["case_id", "status", "metadata", "abstract", "sections", "text_blocks",
                       "answers", "ocr_quality", "uncertainties"],
        },
    },
]


def make_scan(src, dst, pages, dpi):
    s = fitz.open(src)
    d = fitz.open()
    for i in range(min(pages, len(s))):
        pix = s[i].get_pixmap(dpi=dpi, colorspace=fitz.csGRAY)
        p = d.new_page(width=s[i].rect.width, height=s[i].rect.height)
        p.insert_image(p.rect, stream=pix.tobytes("png"))
    d.save(dst, deflate=True)
    assert not d[0].get_text().strip(), "scan must have no text layer"


def main():
    os.makedirs(OUT, exist_ok=True)
    task_md = open(TASK_MD, encoding="utf-8").read()
    for c in CASES:
        cdir = os.path.join(OUT, c["case_id"])
        os.makedirs(cdir, exist_ok=True)
        src = os.path.join(CORPUS, c["src"])
        dst = os.path.join(cdir, c["pdf"])
        if c.get("scan_pages"):
            make_scan(src, dst, c["scan_pages"], c["dpi"])
        else:
            shutil.copy(src, dst)
        case = {
            "case_id": c["case_id"],
            "task": c["task"],
            "materials": [{"kind": "pdf", "path": "/input/" + c["pdf"]}],
            "output_contract": c["output_contract"],
            "constraints": {"output_dir": "/app/output", "result_file": "/app/output/result.json"},
        }
        json.dump(case, open(os.path.join(cdir, "case.json"), "w", encoding="utf-8"),
                  ensure_ascii=False, indent=1)
        open(os.path.join(cdir, "task.md"), "w", encoding="utf-8").write(task_md)
        print("built", cdir)


if __name__ == "__main__":
    main()
