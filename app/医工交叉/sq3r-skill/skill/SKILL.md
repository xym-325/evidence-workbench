---
name: sq3r-academic-reading
description: Perform a rigorous SQ3R (Survey–Question–Read–Recite–Review) close reading of ONE scholarly article and produce a structured, evidence-anchored close-reading report. Use when the user asks to 精读 / 深读 / 批判性阅读 a paper, produce reading notes, a literature card, an SQ3R report, or to evaluate how well a model or agent read an article. Do NOT use for quick factual Q&A, one-line summaries, or non-scholarly content.
license: MIT
metadata:
  version: "1.0.0"
  domain: academic-reading
  method: SQ3R
  languages: "zh-en"
  companion: "prompts/sq3r_system_prompt.md"
---

# SQ3R Academic Close-Reading Skill
# SQ3R 学术精读技能

> 把任意**单篇**学术文章，用 SQ3R 五步法转成一份**结构化、可追溯、可自评**的精读报告。
> Turn any **single** scholarly article into a structured, traceable, self-assessed reading report via SQ3R.

---

## 1. 何时使用 / When to use

**触发 / Triggers**
- "精读/深读/细读这篇文章"、"读文献"、"文献卡片"、"SQ3R"、"批判性阅读"。
- English: "close-read this paper", "deep read / critical reading", "SQ3R notes", "literature card".
- 需要评测某个模型/智能体对文章的精读质量（benchmark）。

**不适用 / Do NOT use for**
- 快速事实问答、只要一句话摘要、非学术内容（新闻、博客）。
- 多篇文章的综述（本技能是**单篇**精读；多篇请逐篇调用）。

---

## 2. 输入 / Inputs

| 参数 / Param | 必需 / Required | 说明 / Notes |
|---|---|---|
| `ARTICLE` | 是 / Yes | 单篇文章的路径或全文文本（txt/md/PDF 转文本） |
| `READER_GOAL` | 否 / No | 读者动机/关注点；缺省为"理解核心贡献与方法" |
| `LANG` | 否 / No | 输出语言；缺省与文章一致 |
| `MODE` | 否 / No | `standard`（默认）｜`strict`（见 §7） |

---

## 3. 工作流 / Workflow（分步流程 / Step-by-step）

### Step 0 — 预处理 / Prepare
- 确认文章完整、可读；记录标题、作者、载体、年份、篇幅。
- 设定 `READER_GOAL` 与输出语言。
- **输出**：内部工作变量（不直接交付）。

### Step 1 — S：Survey 浏览 / Survey
- 用约 10% 精力做**全局扫描**，不逐字细读。
- **产出**：(a) 元信息表；(b) 结构地图（章节→逻辑角色）；(c) 图表清单（仅凭标题/上下文）；(d) 一句话定位。
- **禁止**：此时臆测正文细节。

### Step 2 — Q：Question 提问 / Question
- 把标题与摘要改写成 **6–10 个研究问题**，覆盖：动机、假设、方法选择、关键定义、证据强度、局限、贡献。
- 每个问题标注"**预期在原文何处被回答**"。
- **产出**：问题清单表（编号｜问题｜预期出处）。

### Step 3 — R：Read 精读 / Read
- 带着 Q 逐节精读，**逐题作答**。
- 每答附：**答案 + 原文出处（章节/段落/图表）**；解释关键公式/定义/指标。
- **分离**作者主张（claim）与支撑证据（evidence）；证据不足处显式标注。
- 记录任何矛盾、术语不清、前后不一致之处。
- **产出**：答案表（问题编号｜答案｜原文出处｜证据强度）。

### Step 4 — R：Recite 复述 / Recite
- **合上原文**，用自己的话复述（费曼式），不得照抄原句。
- **产出**：(a) 3–5 句核心思想；(b) 论证链 claim→evidence→conclusion；(c) 关键术语自解。
- **回查核对**：与原文比对，发现不符即标注并更正。

### Step 5 — R：Review 回顾 / Review
- 跳出细节做元认知回顾。
- **产出**：(a) 5 道闭卷自测题 + 答案要点；(b) 批判性评价（假设/外推边界/可复现性/局限与伦理）；(c) 迁移与关联；(d) **置信度自评表**（高/中/低 + 基于证据强度的理由）。

### Step 6 — 自检与输出 / Self-check & Output
- 运行 §6 质检清单，全部通过后按 §5 模板输出。

---

## 4. 证据锚点约定 / Evidence-Anchor Convention

- 首选**语义锚点**（可移植）：`[§3.2.1]`、`[§Table 2]`、`[§Fig.3]`、`[§Abstract]`。
- 纯文本无编号时可用行号锚点 `[L55]`，但须注明其绑定具体文件。
- **规则**：任何事实性断言都必须带锚点；无锚点的断言视为**未通过质检**。
- 外部知识（原文未含）须标 `[外部知识]` 并单列。

---

## 5. 输出模板 / Output Template

```
# 精读报告 / Close-Reading Report
## 0. 元信息 / Metadata
## 1. Survey 浏览            （结构地图、图表清单、一句话定位）
## 2. Question 提问          （表：编号 | 问题 | 预期出处）
## 3. Read 精读              （表：编号 | 答案 | 原文出处 | 证据强度；+ 关键公式/指标；+ 歧义记录）
## 4. Recite 复述            （核心思想 / 论证链 / 术语自解 / 回查核对）
## 5. Review 回顾            （自测题 / 批判性评价 / 迁移关联 / 置信度自评表）
## 6. 一句话总结 / One-line Takeaway
```

---

## 6. 质量闸门 / Quality Gates（输出前逐条自检）

- [ ] S/Q/R/R/R 五部分齐全，Q 的每个问题在 R 中都有答案与锚点。
- [ ] 所有事实性断言均带原文锚点；无编造引用/数据。
- [ ] 关键数值与原文逐字一致；原文自身矛盾处已标注（不私自择一）。
- [ ] 明确区分"作者主张"与"支撑证据"。
- [ ] Recite 为自述而非抄袭，且完成回查核对。
- [ ] Review 含置信度自评，理由基于证据强度。
- [ ] 未引入未标注的外部知识。

---

## 7. 模式 / Modes

| 模式 / Mode | 额外要求 / Additional requirements | 适用 / Best for |
|---|---|---|
| `standard` | §1–§6 基线要求 | 常规精读、读书笔记、文献卡片 |
| `strict` | ① 断言强制 `[§]` 锚点；② 数值逐字校验并注明出处；③ **反例审查 ≥3 项**；④ 置信度量化（高/中/低+理由）；⑤ 末尾**可审计质检清单**；⑥ 禁止未标注外部知识 | 审稿、复现性检查、矛盾点排查、高风险审计 |

---

## 8. 可选扩展：多智能体精读评测 / Optional: Multi-Agent Benchmarking

在同一文章上运行 `standard` 与 `strict`（及不含 SQ3R 的**基线**），按以下 rubric（0–10，加权）评分：

| 维度 / Dimension | 权重 |
|---|---|
| S 浏览全局 | 10% |
| Q 提问质量 | 10% |
| R 精读深度 | 20% |
| R 复述质量 | 15% |
| R 回顾元认知 | 15% |
| 事实准确性 / Grounding | 15% |
| 可追溯性 / Traceability | 10% |
| 结构与可读性 | 5% |

辅助客观指标：词数、锚点数、表格数、**是否发现原文级不一致**。可据此对"不同模型/智能体"排名并给出优劣结论。

---

## 9. 安装与复用 / Install & Reuse

- **opencode**：本文件即技能本体，置于 `.opencode/skills/sq3r-academic-reading/SKILL.md`（项目级）或 `~/.config/opencode/skills/...`（全局）。保存后**需重启 opencode** 生效。
- **作为系统提示词**：把 `prompts/sq3r_system_prompt.md` 注入任意模型/智能体即可复现。
- **跨模型复现**：将同一 `ARTICLE` 与系统提示词投喂到不同厂商模型，套用 §8 rubric 评测。

---

## 10. 参考 / References

- 系统提示词模板 / System prompt：`prompts/sq3r_system_prompt.md`
- 严格模式增补 / Strict addendum：`prompts/agent_c_sq3r_strict.md`
- 基线对照 / Baseline control：`prompts/agent_a_baseline.md`
- 示例成品 / Worked examples：`outputs/agent_b_sq3r_standard.md`、`outputs/agent_c_sq3r_strict.md`
