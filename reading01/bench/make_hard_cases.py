#!/usr/bin/env python3
"""Build hard-layout sample PDFs with exact ground truth (xelatex + Pillow).

  python3 make_hard_cases.py OUTDIR

Writes OUTDIR/<case>.pdf and OUTDIR/<case>.gt.json for:
  tables_hard   row spans (\\multirow centred + top-aligned), wrapped cell text,
                an uncaptioned ruled table, a longtable continued on the next page,
                and a table that is only an embedded image (needs OCR)
  zh_noabstract Chinese paper whose abstract has no "摘要" label
  short_furniture  one-page document: journal line, DOI line and copyright line that
                appear once (no repetition to learn from) + page number
  scan_mixed    image-only PDF: page 1 English, pages 2-3 Chinese (OCR language per page)
The ground truth is written from the same Python data that generates the LaTeX, so it
is exact. selftest.py checks the parser against it.
"""
import json
import os
import subprocess
import sys

FONT = "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"

ROWSPAN = {"header": ["Model", "Setting", "Acc", "F1"],
           "groups": [("Base", [("lr 1e-4", "81.2", "79.0"), ("lr 3e-4", "82.5", "80.1"), ("lr 1e-3", "80.9", "78.4")], "centred"),
                      ("Large", [("lr 1e-4", "84.0", "82.2"), ("lr 3e-4", "85.1", "83.0")], "centred"),
                      ("Huge", [("lr 1e-4", "86.3", "84.7"), ("lr 3e-4", "86.9", "85.2")], "top")]}
WRAP = {"header": ["Method", "Description", "Score"],
        "rows": [("Alpha", "linear probe on frozen features with early stopping", "71.3"),
                 ("Beta", "full fine-tuning of all layers with layer-wise decay", "78.6"),
                 ("Gamma", "adapter modules inserted after every attention block", "77.9")]}
UNCAP = {"header": ["Split", "Images", "Classes"],
         "rows": [("train", "50000", "10"), ("val", "5000", "10"), ("test", "10000", "10")]}
LONG = {"header": ["Run", "Temp (K)", "Yield (%)"],
        "rows": [("R%02d" % i, str(280 + 5 * i), "%.1f" % (40 + 1.7 * i)) for i in range(1, 61)]}
IMG = {"header": ["Catalyst", "Conversion", "Selectivity"],
       "rows": [("Pt/C", "92.4", "88.1"), ("Pd/C", "85.7", "91.3"), ("Ni/SiO2", "63.2", "77.8")]}


def para(i):
    return ("Paragraph %d describes the experimental protocol in plain prose so that the layout looks like a "
            "real paper; the sentences are long enough to wrap over several lines of the column and they end "
            "with a full stop." % i)


def image_table(path):
    from PIL import Image, ImageDraw, ImageFont
    f = ImageFont.truetype(FONT, 40)
    rows = [IMG["header"]] + [list(r) for r in IMG["rows"]]
    xs = [40, 420, 760]
    W, H = 1100, 90 + 80 * len(rows)
    im = Image.new("L", (W, H), 255)
    dr = ImageDraw.Draw(im)
    dr.line([(20, 20), (W - 20, 20)], fill=0, width=4)
    for k, r in enumerate(rows):
        y = 40 + 80 * k
        for x, t in zip(xs, r):
            dr.text((x, y), t, fill=0, font=f)
        if k == 0:
            dr.line([(20, y + 62), (W - 20, y + 62)], fill=0, width=2)
    dr.line([(20, H - 20), (W - 20, H - 20)], fill=0, width=4)
    im.save(path, dpi=(300, 300))


def tex_esc(t):
    return t.replace("%", r"\%").replace("_", r"\_").replace("&", r"\&")


def tables_tex():
    L = [r"\documentclass[11pt]{article}", r"\usepackage[a4paper,margin=2.2cm]{geometry}",
         r"\usepackage{booktabs,multirow,longtable,graphicx,array}", r"\begin{document}",
         r"\section{Row spans}", para(1), "",
         r"\begin{table}[h]\centering\caption{Results by model size}",
         r"\begin{tabular}{llcc}\toprule", " & ".join(ROWSPAN["header"]) + r"\\\midrule"]
    for name, rows, how in ROWSPAN["groups"]:
        for k, r in enumerate(rows):
            if how == "centred":
                lab = (r"\multirow{%d}{*}{%s}" % (len(rows), name)) if k == 0 else ""
            else:
                lab = name if k == 0 else ""
            L.append("%s & %s \\\\" % (lab, " & ".join(r)))
        L.append(r"\midrule" if name != ROWSPAN["groups"][-1][0] else r"\bottomrule")
    L += [r"\end{tabular}\end{table}", "", para(2), "", r"\section{Wrapped cells}", para(3), "",
          r"\begin{table}[h]\centering\caption{Fine-tuning strategies}",
          r"\begin{tabular}{l p{5.2cm} c}\toprule", " & ".join(WRAP["header"]) + r"\\\midrule"]
    for r in WRAP["rows"]:
        L.append(" & ".join(r) + r"\\")
    L += [r"\bottomrule\end{tabular}\end{table}", "", para(4), "",
          r"\section{A table without caption}", para(5), "",
          r"\begin{center}\begin{tabular}{lrr}\toprule", " & ".join(UNCAP["header"]) + r"\\\midrule"]
    for r in UNCAP["rows"]:
        L.append(" & ".join(r) + r"\\")
    L += [r"\bottomrule\end{tabular}\end{center}", "", para(6), "",
          r"\section{Image table}", para(7), "",
          r"\begin{table}[h]\centering\caption{Catalyst screening (scanned table)}",
          r"\includegraphics[width=0.62\textwidth]{img_table.png}\end{table}", "", para(8), "",
          r"\section{Long table}", para(9), "",
          r"\begin{longtable}{lcc}", r"\caption{Reaction runs}\\\toprule",
          " & ".join(tex_esc(h) for h in LONG["header"]) + r"\\\midrule\endfirsthead",
          r"\caption[]{Reaction runs (continued)}\\\toprule",
          " & ".join(tex_esc(h) for h in LONG["header"]) + r"\\\midrule\endhead",
          r"\bottomrule\endlastfoot"]
    for r in LONG["rows"]:
        L.append(" & ".join(r) + r"\\")
    L += [r"\end{longtable}", "", para(10), r"\end{document}"]
    return "\n".join(L)


ZH_ABS = ("本文提出一种基于交联网络的固态聚合物电解质，通过调节交联剂含量同时提高离子电导率与力学强度。"
          "实验表明，交联度为百分之十时室温电导率达到每厘米一点三七毫西门子，拉伸强度提高约百分之五十，"
          "并在两百次循环后保持百分之九十五的容量。该方法为高安全性锂金属电池提供了一种简单可行的电解质设计思路。")


def zh_tex():
    body = []
    for k, (num, title) in enumerate([("1", "引言"), ("2", "实验部分"), ("3", "结果与讨论"), ("4", "结论")]):
        body.append(r"\section{%s}" % title)
        for j in range(2):
            body.append("第%d节第%d段：本研究考察了聚合物电解质膜的结构与性能之间的关系，样品分别在不同温度下测试三次，"
                        "结果取平均值并给出标准偏差，与对照组相比变化趋势更加明显。" % (k + 1, j + 1))
            body.append("")
    return "\n".join([r"\documentclass[twocolumn,10pt]{ctexart}", r"\usepackage[a4paper,margin=2cm]{geometry}",
                      r"\title{交联固态聚合物电解质的设计与性能}", r"\author{测试作者\\某某大学化学学院}", r"\date{}",
                      r"\begin{document}", r"\twocolumn[\maketitle\begin{center}\begin{minipage}{0.86\textwidth}",
                      ZH_ABS, r"\end{minipage}\end{center}\vspace{1em}]"] + body + [r"\end{document}"])


SHORT = {"journal": "Journal of Test Studies, Vol. 3, No. 2 (2025) 101-101",
         "doi": "https://doi.org/10.1234/jts.2025.0101",
         "copyright": "© 2025 The Authors. Published under the CC BY 4.0 licence."}


def short_tex():
    return "\n".join([
        r"\documentclass[11pt]{article}", r"\usepackage[a4paper,margin=2.5cm]{geometry}", r"\usepackage{fancyhdr}",
        r"\pagestyle{fancy}\fancyhf{}\renewcommand{\headrulewidth}{0pt}",
        r"\fancyhead[L]{\footnotesize %s}" % SHORT["journal"], r"\fancyhead[R]{\footnotesize %s}" % SHORT["doi"].replace("_", r"\_"),
        r"\fancyfoot[L]{\footnotesize %s}" % SHORT["copyright"], r"\fancyfoot[R]{\footnotesize 101}",
        r"\begin{document}", r"\begin{center}{\LARGE A One-Page Note on Testing}\\[1em] A. Author\end{center}",
        r"\section{Introduction}", para(1), "", para(2), r"\section{Method}", para(3), "", para(4), r"\end{document}"])


def xelatex(out, name, src):
    with open(os.path.join(out, name + ".tex"), "w", encoding="utf-8") as fh:
        fh.write(src)
    for _ in range(3):
        subprocess.run(["xelatex", "-interaction=nonstopmode", name + ".tex"], cwd=out,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=300)
    return os.path.exists(os.path.join(out, name + ".pdf"))


def scan_mixed(out, en_pdf, zh_pdf):
    """Image-only PDF from rendered pages of the English and Chinese bench PDFs."""
    from PIL import Image
    imgs = []
    for pdf, pno in ((en_pdf, 1), (zh_pdf, 2), (zh_pdf, 3)):
        base = os.path.join(out, "scan_src_%d" % len(imgs))
        subprocess.run(["pdftoppm", "-f", str(pno), "-l", str(pno), "-r", "200", "-gray", "-png", "-singlefile", pdf, base],
                       check=True, timeout=120)
        imgs.append(Image.open(base + ".png").convert("L"))
    p = os.path.join(out, "scan_mixed.pdf")
    imgs[0].save(p, save_all=True, append_images=imgs[1:], resolution=200.0)
    return p


def main(out):
    os.makedirs(out, exist_ok=True)
    image_table(os.path.join(out, "img_table.png"))
    ok = xelatex(out, "tables_hard", tables_tex())
    gt = {"rowspan": {"caption": "Results by model size", "header": ROWSPAN["header"],
                      "rows": [[name] + list(r) for name, rows, _ in ROWSPAN["groups"] for r in rows],
                      "spans": [(name, len(rows), how) for name, rows, how in ROWSPAN["groups"]]},
          "wrap": {"caption": "Fine-tuning strategies", "header": WRAP["header"], "rows": [list(r) for r in WRAP["rows"]]},
          "uncaptioned": {"header": UNCAP["header"], "rows": [list(r) for r in UNCAP["rows"]]},
          "image": {"caption": "Catalyst screening", "header": IMG["header"], "rows": [list(r) for r in IMG["rows"]]},
          "long": {"caption": "Reaction runs", "header": LONG["header"], "rows": [list(r) for r in LONG["rows"]]}}
    with open(os.path.join(out, "tables_hard.gt.json"), "w", encoding="utf-8") as fh:
        json.dump(gt, fh, ensure_ascii=False, indent=1)
    ok &= xelatex(out, "zh_noabstract", zh_tex())
    with open(os.path.join(out, "zh_noabstract.gt.json"), "w", encoding="utf-8") as fh:
        json.dump({"title": "交联固态聚合物电解质的设计与性能", "abstract": ZH_ABS,
                   "first_body_paragraph_starts": "第1节第1段"}, fh, ensure_ascii=False, indent=1)
    ok &= xelatex(out, "short_furniture", short_tex())
    with open(os.path.join(out, "short_furniture.gt.json"), "w", encoding="utf-8") as fh:
        json.dump(dict(SHORT, page_number="101"), fh, ensure_ascii=False, indent=1)
    here = os.path.dirname(os.path.abspath(__file__))
    en = os.path.join(out, "bench_en.pdf")
    zh = os.path.join(out, "bench_zh.pdf")
    if not (os.path.exists(en) and os.path.exists(zh)):
        subprocess.run([sys.executable, os.path.join(here, "make_bench.py"), out], check=True, timeout=900)
    scan_mixed(out, en, zh)
    with open(os.path.join(out, "scan_mixed.gt.json"), "w", encoding="utf-8") as fh:
        json.dump({"page_lang": {"1": "eng", "2": "chi_sim+eng", "3": "chi_sim+eng"}}, fh, indent=1)
    print("built hard cases in", out, "ok" if ok else "(some LaTeX builds failed)")


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "out_hard")
