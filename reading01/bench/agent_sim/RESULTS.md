# Agent 端到端对照模拟（本地，非平台成绩）

平台的 DeepSeek Harness 与 3 个正式案例不公开。本目录按题面写明的运行约定做了一个近似环境，用来比较“基础 Agent”和“加载本 Skill 的 Agent”。它不是官方评测，数字只说明方向与量级。

## 文件

| 文件 | 作用 |
|---|---|
| `Dockerfile.txt`（构建时用 `docker build -f Dockerfile.txt .`） | 近似评测镜像：python 3.11、poppler-utils、tesseract（eng、chi_sim）、PyMuPDF、pdfplumber、Pillow、numpy、pandas |
| `make_cases.py` | 从公开 arXiv 论文生成 3 个模拟案例（`/input/case.json` + `task.md` + PDF）；C 案例把 BERT 前 3 页渲染成无文字层的扫描件 |
| `harness.py` | Agent 循环：OpenAI 兼容的 DeepSeek 接口，工具 bash / read_file / write_file，预算 64 次请求 / 900 秒 / 每次回复 16384 token；`--condition skill` 时把 Skill 只读挂载到 `/skills/weekly-skill` 并在系统提示中列出 Skill，`base` 时不挂载；记录 trace.jsonl 与 summary.json |
| `check.py` | 作者自写的标准答案，逐项核对每次运行的 result.json，并汇总每个（案例, 条件）的平均值 |
| `run_matrix.sh` | 并发跑 案例 × 条件 × 重复次数，最后调用 check.py |

## 复现

```bash
cd bench/agent_sim
docker build -t reading01-sim .
mkdir -p corpus && for id in 1706.03762 1512.03385 1810.04805; do curl -L -o corpus/$id.pdf https://arxiv.org/pdf/$id; done
docker run --rm -v $PWD:/sim -v $PWD/corpus:/corpus:ro -v /path/to/task.md:/task.md:ro reading01-sim python3 /sim/make_cases.py
DEEPSEEK_API_KEY=... ./run_matrix.sh deepseek-flash 3 6 skill base   # 模型名、重复次数、并发数、条件
```

`task.md` 用题面原文（与正式运行一样，它是面向 Skill 作者的总题面，案例任务在 case.json 中）。arXiv 论文版本会更新，`check.py` 的标准答案按 2026-11 下载的版本编写。

## 结果（deepseek-flash，每个条件 3 次，6 路并发）

| 案例 | 条件 | 通过检查 | success | 平均请求数 | 平均用时 |
|---|---|---|---|---|---|
| A Transformer 结构化 | 基础 Agent | 33% | 1/3 | 63.7 | 669 s |
| A | Skill v1.2 | 100% | 3/3 | 46.0 | 244 s |
| A | Skill v1.3 | 100% | 3/3 | 29.3 | 210 s |
| B ResNet 表格问答 | 基础 Agent | 100% | 3/3 | 53.3 | 438 s |
| B | Skill v1.2 | 100% | 3/3 | 19.7 | 106 s |
| B | Skill v1.3 | 100% | 3/3 | 23.3 | 97 s |
| C BERT 扫描件 | 基础 Agent | 0% | 0/3 | 22.0 | 903 s（全部超时） |
| C | Skill v1.0 | 78% | 1/3 | 12.7 | 780 s |
| C | Skill v1.2 | 100% | 2/3 | 30.3 | 226 s |
| C | Skill v1.3 | 100% | 3/3 | 29.0 | 209 s |

基础 Agent 的数字来自 2026-11-27 的一批（基础条件不依赖 Skill 版本）；Skill 各版本分别在 2026-11-27/28 运行。同一版本不同批次之间请求数可相差 10 次以上。

## 局限

- 模拟 harness 的系统提示、工具与平台不一定相同；模型为 deepseek-flash，平台模型可能不同。
- 3 个案例、每个 3 次，样本很小；`check.py` 只检查关键事实与结构是否出现，不评判表述质量。
