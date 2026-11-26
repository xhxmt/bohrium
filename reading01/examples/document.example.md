<!-- 示例输出（节选）：document.md 第 1 页开头与第 7 页表 2，非正式案例结果 -->
<!-- parsed by pdfdoc: paper.pdf | sha256 5692a5514787a8c6 | pages 1-16 | backend pymupdf -->
<!-- every block is preceded by an anchor comment: block-id page [x0,y0,x1,y1] type; coordinates are PDF points from the top-left corner -->

<!-- ===== page 1 ===== -->
<!-- p1-b0 p1 [116,71,481,101] title -->
# BERT: Pre-training of Deep Bidirectional Transformers for Language Understanding

<!-- p1-b1 p1 [122,131,290,143] front_matter -->
Jacob Devlin Ming-Wei Chang

<!-- p1-b2 p1 [305,131,479,143] front_matter -->
Kenton Lee Kristina Toutanova

<!-- p1-b3 p1 [108,145,493,170] front_matter -->
Google AI Language {jacobdevlin,mingweichang,kentonl,kristout}@google.com

<!-- p1-b4 p1 [159,224,203,236] heading level=1 -->
## Abstract

<!-- p1-b5 p1 [89,246,273,423] abstract -->
We introduce a new language representation model called BERT, which stands for Bidirectional Encoder Representations from Transformers. Unlike recent language representation models (Peters et al., 2018a; Radford et al., 2018), BERT is designed to pre-train deep bidirectional representations from unlabeled text by jointly conditioning on both left and right context in all layers. As a result, the pre-trained BERT model can be fine-tuned with just one additional output layer to create state-of-the-art models for a wide range of tasks, such as question answering and language inference, without substantial task-specific architecture modifications.


...

<!-- ===== page 7 ===== -->
<!-- p7-b0 p7 [72,67,290,285] table -->
**Table 2.** SQuAD 1.1 results. The BERT ensemble is 7x systems which use different pre-training check- points and fine-tuning seeds.

| System | Dev / EM | Dev / F1 | Test / EM | Test / F1 |
|---|---|---|---|---|
| Top Leaderboard Systems (Dec 10th, 2018) |  |  |  |  |
| Human | - | - | 82.3 | 91.2 |
| #1 Ensemble - nlnet | - | - | 86.0 | 91.7 |
| #2 Ensemble - QANet | - | - | 84.5 | 90.5 |
| Published |  |  |  |  |
| BiDAF+ELMo (Single) | - | 85.6 | - | 85.8 |
| R.M. Reader (Ensemble) | 81.2 | 87.9 | 82.3 | 88.5 |
| Ours |  |  |  |  |
| BERT<sub>BASE</sub> (Single) | 80.8 | 88.5 | - | - |
| BERT<sub>LARGE</sub> (Single) | 84.1 | 90.9 | - | - |
| BERT<sub>LARGE</sub> (Ensemble) | 85.8 | 91.8 | - | - |
| BERT<sub>LARGE</sub> (Sgl.+TriviaQA) | 84.2 | 91.1 | 85.1 | 91.8 |
| BERT<sub>LARGE</sub> (Ens.+TriviaQA) | 86.2 | 92.2 | 87.4 | 93.2 |

<!-- table-2: 15 rows x 5 cols, header rows 2; files: tables/table-2.csv, tables/table-2.html -->
![table-2](crops/table-2.png)

<!-- check: row 1: 4 of 5 columns filled (empty cells or wrapped text) -->
<!-- p7-b1 p7 [72,303,290,470] table -->
**Table 3.** SQuAD 2.0 results. We exclude entries that use BERT as one of their components.

| System | Dev / EM | Dev / F1 | Test / EM | Test / F1 |
|---|---|---|---|---|
| Top Leaderboard Systems (Dec 10th, 2018) |  |  |  |  |
