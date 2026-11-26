#!/usr/bin/env python3
"""Build a small ground-truth benchmark PDF (Chinese, two columns) with xelatex.

Everything that a parser should recover is generated from one Python structure,
so the ground truth (gt.json) is exact: paragraph order, headings + levels,
table cells (multi-level header), numbered equations, footnote, running
header/footer, references.

  python3 make_bench.py OUTDIR        -> OUTDIR/bench_zh.pdf, OUTDIR/bench_zh.tex, OUTDIR/gt.json
"""
import json
import os
import subprocess
import sys

HEADER = "北化测试学报 · 第1卷"
TOPICS = ["离子电导率", "玻璃化转变温度", "拉伸强度", "吸水率", "热稳定性", "界面阻抗", "结晶度", "溶胀比"]


EN_TOPICS = ["ionic conductivity", "glass transition temperature", "tensile strength", "water uptake",
             "thermal stability", "interfacial impedance", "crystallinity", "swelling ratio"]


def para_en(i, topic):
    return ("Paragraph %d: this study examines the %s of crosslinked polymer electrolyte membranes. "
            "Each specimen was measured three times at 25 and 60 degrees Celsius, and the mean value is "
            "reported together with its standard deviation. Compared with the uncrosslinked control, the "
            "%s changes more strongly as the crosslink density increases, which is consistent with the "
            "restricted segmental motion discussed in reference [%d]. To exclude accidental errors we repeated "
            "all measurements on a second batch, and the conclusions remained unchanged." % (i, topic, topic, (i % 4) + 1))


def para(i, topic):
    return ("第%d段：本研究考察了聚合物电解质膜的%s。样品在 25 °C 与 60 °C 下分别测试三次，"
            "结果取平均值并给出标准偏差。与对照组相比，交联密度提高后%s的变化趋势更加明显，"
            "这一现象与链段运动受限的解释一致（见文献[%d]）。为避免偶然误差，"
            "我们在第二批样品上重复了全部实验，结论保持不变。" % (i, topic, topic, (i % 4) + 1))


def build(lang="zh"):
    body = []  # (kind, payload) in reading order
    sec = [("1", "引言", 1), ("2", "实验部分", 1), ("2.1", "材料与制备", 2), ("2.2", "表征方法", 2),
           ("3", "结果与讨论", 1), ("3.1", "电导率", 2), ("3.2", "力学性能", 2), ("4", "结论", 1)]
    if lang == "en":
        sec = [("1", "Introduction", 1), ("2", "Experimental Section", 1), ("2.1", "Materials and Preparation", 2),
               ("2.2", "Characterization", 2), ("3", "Results and Discussion", 1), ("3.1", "Ionic Conductivity", 2),
               ("3.2", "Mechanical Properties", 2), ("4", "Conclusions", 1)]
    P = para_en if lang == "en" else para
    T = EN_TOPICS if lang == "en" else TOPICS
    n = 1
    for k, (num, title, lvl) in enumerate(sec):
        body.append(("heading", {"number": num, "title": title, "level": lvl}))
        count = 3 if lvl == 2 else 2
        for _ in range(count):
            body.append(("para", P(n, T[n % len(T)])))
            n += 1
        if num == "2.2":
            body.append(("equation", {"number": "1", "latex": r"\sigma = \frac{L}{R_b \, S}"}))
            body.append(("para", P(n, "bulk resistance" if lang == "en" else "体相电阻")))
            n += 1
            body.append(("equation", {"number": "2", "latex": r"E_a = -k_B \frac{\partial \ln \sigma}{\partial (1/T)}"}))
        if num == "3.1":
            body.append(("table", None))
    table = {
        "number": "1", "caption": "不同交联度样品的性能对比",
        "header": [["样品", "电导率/(mS·cm⁻¹)", "", "力学性能", ""],
                   ["", "25 °C", "60 °C", "强度/MPa", "伸长率/%"]],
        "rows": [["PEM-0", "0.82", "2.31", "12.5", "310"],
                 ["PEM-5", "1.05", "2.96", "15.1", "275"],
                 ["PEM-10", "1.37", "3.48", "18.7", "240"],
                 ["PEM-15", "1.21", "3.02", "21.3", "190"]],
    }
    refs = ["张三, 李四. 聚合物电解质研究进展. 高分子学报, 2020, 51(3): 1-10.",
            "Wang L, Chen Y. Ion transport in crosslinked membranes. Macromolecules, 2019, 52: 2345-2356.",
            "Smith J, et al. Glass transition in polymer electrolytes. J Polym Sci, 2018, 56: 77-89.",
            "刘五. 膜材料力学性能测试方法. 北京: 化学工业出版社, 2017."]
    return body, table, refs


def tex(body, table, refs, lang="zh"):
    cls = r"\documentclass[twocolumn,10pt]{article}\usepackage{fontspec}" if lang == "en" else r"\documentclass[twocolumn,10pt]{ctexart}"
    L = [cls,
         r"\usepackage[a4paper,margin=2cm]{geometry}",
         r"\usepackage{amsmath,booktabs,multirow,fancyhdr}",
         r"\pagestyle{fancy}\fancyhf{}\fancyhead[C]{%s}\fancyfoot[C]{第 \thepage\ 页}" % HEADER,
         r"\renewcommand{\headrulewidth}{0.4pt}",
         (r"\ctexset{section={format=\large\bfseries\raggedright},subsection={format=\normalsize\bfseries\raggedright}}" if lang != "en" else ""),
         r"\title{交联结构对聚合物电解质膜性能的影响}", r"\author{测试作者\thanks{北京化工大学 材料科学与工程学院}}",
         r"\date{}", r"\begin{document}", r"\maketitle", r"\thispagestyle{fancy}"]
    L.append(r"\noindent\textbf{摘要：}本文以交联度为变量，系统测试了聚合物电解质膜的电导率与力学性能，"
             r"结果表明适度交联可同时提高两类性能。")
    L.append("")
    for kind, p in body:
        if kind == "heading":
            L.append(("\\section{%s}" if p["level"] == 1 else "\\subsection{%s}") % p["title"])
        elif kind == "para":
            L.append(p + "\n")
        elif kind == "equation":
            L.append(r"\begin{equation}%s\label{eq%s}\end{equation}" % (p["latex"], p["number"]))
        elif kind == "table":
            h1, h2 = table["header"]
            L += [r"\begin{table}[t]", r"\centering\small", r"\caption{%s}" % table["caption"],
                  r"\begin{tabular}{lcccc}", r"\toprule",
                  r"\multirow{2}{*}{%s} & \multicolumn{2}{c}{电导率/(mS$\cdot$cm$^{-1}$)} & \multicolumn{2}{c}{%s}\\" % (h1[0], h1[3]),
                  r"\cmidrule(lr){2-3}\cmidrule(lr){4-5}",
                  r" & 25 °C & 60 °C & 强度/MPa & 伸长率/\%\\", r"\midrule"]
            for r in table["rows"]:
                L.append(" & ".join(r) + r"\\")
            L += [r"\bottomrule", r"\end{tabular}", r"\end{table}"]
    L.append(r"\begin{thebibliography}{9}")
    for i, r in enumerate(refs):
        L.append(r"\bibitem{r%d} %s" % (i + 1, r))
    L += [r"\end{thebibliography}", r"\end{document}"]
    return "\n".join(L)


def main(out, lang="zh"):
    os.makedirs(out, exist_ok=True)
    body, table, refs = build(lang)
    name = "bench_%s" % lang
    src = tex(body, table, refs, lang)
    if lang == "en":
        src = (src.replace(HEADER, "Test Journal of Polymer Membranes, Vol. 1").replace("第 \\thepage\\ 页", "Page \\thepage")
               .replace("交联结构对聚合物电解质膜性能的影响", "Effect of Crosslinking on Polymer Electrolyte Membranes")
               .replace("测试作者", "Test Author").replace("北京化工大学 材料科学与工程学院", "Beijing University of Chemical Technology")
               .replace(r"\textbf{摘要：}本文以交联度为变量，系统测试了聚合物电解质膜的电导率与力学性能，"
                        r"结果表明适度交联可同时提高两类性能。",
                        r"\textbf{Abstract.} We vary the crosslink density and measure conductivity and mechanical "
                        r"properties of polymer electrolyte membranes; moderate crosslinking improves both."))
        src = src.replace(table["caption"], "Properties of samples with different crosslinking degree")
    with open(os.path.join(out, name + ".tex"), "w", encoding="utf-8") as fh:
        fh.write(src)
    for _ in range(2):
        subprocess.run(["xelatex", "-interaction=nonstopmode", name + ".tex"], cwd=out,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=300)
    gt = {"header": HEADER if lang != "en" else "Test Journal of Polymer Membranes, Vol. 1",
          "footer_regex": r"第\s*\d+\s*页" if lang != "en" else r"Page\s+\d+",
          "paragraphs": [p for k, p in body if k == "para"],
          "headings": [p for k, p in body if k == "heading"],
          "equations": [p["number"] for k, p in body if k == "equation"],
          "table": table, "references": refs}
    with open(os.path.join(out, name + ".gt.json"), "w", encoding="utf-8") as fh:
        json.dump(gt, fh, ensure_ascii=False, indent=1)
    print("built", os.path.join(out, name + ".pdf"), "exists:", os.path.exists(os.path.join(out, name + ".pdf")))


if __name__ == "__main__":
    out = sys.argv[1] if len(sys.argv) > 1 else "out"
    for lg in (sys.argv[2:] or ["zh", "en"]):
        main(out, lg)
