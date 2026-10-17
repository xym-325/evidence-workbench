# -*- coding: utf-8 -*-
"""write_review：先提纲、后正文；正文引用只能来自「纳入文献表」。

版本说明：本版为模板化生成（题录 + 摘要句按必答问题结构化组织），
目的是产出「可核验的综述初稿」而非自动长文。真正的 LLM 深度改写是 v2 任务。
"""
import re

from .screen_papers import _contains, _haystack, _rule_keywords

SYNTH = [
    "综合来看，围绕「{q}」的讨论集中在数据分布差异与评价口径两方面，结论随设备、中心与人群而变化。",
    "就「{q}」而言，现有研究多在单一数据集或单中心回顾性数据上给出结论，跨机型、跨中心的可比证据仍然有限。",
    "从方法层面看，「{q}」与域偏移建模、外部验证的严谨性以及工作流指标定义紧密相关，需要统一口径后再做横向比较。",
    "值得注意的是，上述证据多为自动化指标对比，尚缺少与阅片工作量、假阳性代价挂钩的临床终点评价。",
]


def _count_words(text):
    text = text or ""
    return len(re.findall(r"[\u4e00-\u9fff]", text)) + len(re.findall(r"[A-Za-z0-9]+", text))


def _sentences(text, limit=8):
    text = (text or "").strip()
    if not text:
        return []
    parts = re.split(r"(?<=[.!?])\s+|(?<=[。！？])", text)
    out = []
    for part in parts:
        part = part.strip()
        if len(part) < 15:
            continue
        out.append(part if len(part) <= 240 else part[:240] + "…")
        if len(out) >= limit:
            break
    return out


def _authors_short(paper):
    authors = paper.get("authors") or []
    if not authors:
        return "佚名"
    return authors[0] if len(authors) == 1 else "%s 等" % authors[0]


def _rank(papers, question):
    """按与子问题的关键词重合度排序，让每个问题的证据顺序有所不同。"""
    kws = _rule_keywords(question, "include")
    scored = []
    for p in papers:
        hay = _haystack(p)
        score = sum(1 for k in kws if _contains(hay, k))
        scored.append((score, p))
    scored.sort(key=lambda x: (-x[0], -(x[1].get("year") or 0)))
    return [p for _, p in scored]


def outline(task):
    lines = ["【综述提纲】", "主题：%s" % task.get("topic")]
    lines.append("0. 方法学声明（数据源 / 时间窗 / 纳入排除 / 局限 / AI 辅助声明）")
    for i, q in enumerate(task.get("must_answer") or [], 1):
        lines.append("%d. %s" % (i, q))
    if (task.get("output") or {}).get("require_unresolved_section", True):
        lines.append("N. 未解决 / 开放问题（写回问题树：主分支 / 叶子）")
    lines.append("N+1. 参考文献")
    return "\n".join(lines)


def _methods_block(task, records, stats):
    srcs = [str(s) for s in (task.get("sources") or [])]
    hit = "；".join("%s %s%s" % (r["source"], r["status"],
                    (" %d 篇" % r["count"]) if r["status"] == "命中"
                    else ("（%s）" % r["detail"] if r["status"] == "请求失败" else ""))
                 for r in records)
    num = "①②③④⑤⑥⑦⑧⑨⑩"
    inc = " ".join("%s%s" % (num[i] if i < len(num) else "%d." % (i + 1), r)
                   for i, r in enumerate(task.get("inclusion") or []))
    exc = " ".join("%s%s" % (num[i] if i < len(num) else "%d." % (i + 1), r)
                   for i, r in enumerate(task.get("exclusion") or []))
    return [
        "> **方法学声明**",
        "> - 数据源与顺序：%s（检索式见检索计划）；" % " / ".join(srcs),
        "> - 检索结果：%s；去重 %d 篇，纳入 %d 篇，排除 %d 篇，最终采用 %d 篇。"
        % (hit or "无", stats["dedup"], stats["included"], stats["excluded"], stats["adopted"]),
        "> - 文献时间窗：%s。" % task.get("years"),
        "> - 纳入标准：%s。" % (inc or "无"),
        "> - 排除标准：%s。" % (exc or "无"),
        "> - 局限：自动关键词初筛不能替代人工阅读全文；跨库题录可能重复或缺摘要；"
        "英文源低于中文关键词时召回受限。",
        "> - AI 辅助声明：本稿由学术智能体模板化生成（题录 + 摘要句结构化组织），"
        "所有引用均来自本次纳入文献表，需人工复核后方可使用。",
    ]


def write_review(task, adopted, records, stats):
    """返回 (markdown, refs)；refs 为参考文献条目（含可回查标识符）。"""
    refs = []
    ref_no = {}
    for i, p in enumerate(adopted, 1):
        ref_no[id(p)] = i
        refs.append({
            "n": i,
            "title": p.get("title") or "",
            "authors": p.get("authors") or [],
            "year": p.get("year"),
            "venue": p.get("venue") or "",
            "source": p.get("source") or "",
            "identifier": p.get("doi") or p.get("url") or "",
        })

    lines = ["# 综述初稿：%s" % task.get("topic"), ""]
    lines += _methods_block(task, records, stats)
    lines += ["", "## 一、正文综述", ""]

    questions = task.get("must_answer") or []
    budget = max(120, int((task.get("output") or {}).get("review_words", 4000)
                          / max(1, len(questions))))

    if not adopted:
        lines.append("（本次检索未得到符合纳入标准的文献，此处仅保留问题框架，暂无可引用的实证内容。）")
        lines.append("")

    for qi, question in enumerate(questions, 1):
        lines.append("### %d. %s" % (qi, question))
        lines.append("")
        if not adopted:
            lines.append("待补充：缺少符合纳入标准的文献，无法给出有据可查的回答。")
            lines.append("")
            continue
        paras, words = [], 0
        for p in _rank(adopted, question):
            if words >= budget:
                break
            sents = _sentences(p.get("abstract"), 2)
            body = " ".join(sents) if sents else "该文献未提供摘要，此处仅采用题录信息"
            para = "%s（%s）在《%s》（%s，来源 %s）中报告：%s [%d]" % (
                _authors_short(p), p.get("year") or "年份不详", p.get("title") or "无题名",
                p.get("venue") or "—", p.get("source") or "—", body, ref_no[id(p)])
            paras.append(para)
            words += _count_words(para)
        paras.append(SYNTH[(qi - 1) % len(SYNTH)].format(q=question.rstrip("？?。. ")))
        lines.append(" ".join(paras))
        lines.append("")

    if (task.get("output") or {}).get("require_unresolved_section", True):
        lines += ["## 二、未解决 / 开放问题（写回问题树）", ""]
        items = ["%s —— 尚无在同一数据与评价口径下的直接对比（问题树：对应主分支下的方法/评价叶子）。"
                 % q.rstrip("？?。. ") for q in questions]
        items += [
            "跨机型、跨剂量协议的公开基准仍不统一，域偏移量化口径缺少共识。",
            "现有证据以回顾性研究为主，前瞻性、多中心外部验证不足，外推需谨慎。",
            "拒识阈值与工作流交接缺少以阅片工作量、假阳性代价为约束的联合评价。",
            "关键词自动初筛存在偏差（中文规则与英文题录的语义鸿沟），需人工阅读复核。",
        ]
        for it in items:
            lines.append("- " + it)
        lines.append("")

    lines += ["## 三、参考文献", ""]
    for r in refs:
        authors = "、".join(r["authors"]) or "佚名"
        ident = ("，%s" % r["identifier"]) if r["identifier"] else "（无 DOI/URL，仅摘要级证据）"
        lines.append("[%d] %s. %s. %s, %s.%s" % (
            r["n"], authors, r["title"], r["venue"] or "—", r["year"] or "年份不详", ident))
    return "\n".join(lines) + "\n", refs
