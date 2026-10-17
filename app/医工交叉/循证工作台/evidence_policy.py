"""Transparent abstract triage and resource policy, not a clinical grading tool.

PRISMA-ScR informs question/eligibility/record keeping; CLAIM informs which
reported methods to expose. Neither defines our 5-20 display range, lexical
coverage, 0.10 marginal yield, or a medical certainty score.
"""
import re
import math

POLICY = {
    "version": "question-map-2026-10-17",
    "display_min": 5, "display_target": 12, "display_max": 20,
    "page_size": 50, "max_pages_per_source": 4,
    "topic_coverage_stop": 1.0, "low_new_relevant_yield": 0.10,
    "low_yield_batches": 2,
    "threshold_basis": "工程启发式/展示预算，尚未验证查全率；不是医学证据充分性阈值",
    "sources": [
        {"name": "PRISMA-ScR", "url": "https://www.prisma-statement.org/scoping"},
        {"name": "Cochrane Handbook 4.4.11", "url": "https://www.cochrane.org/authors/handbooks-and-manuals/handbook/current/chapter-04"},
        {"name": "GRADE / Cochrane Chapter 14", "url": "https://www.cochrane.org/authors/handbooks-and-manuals/handbook/current/chapter-14"},
        {"name": "CLAIM 2024", "url": "https://doi.org/10.1148/ryai.240300"},
        {"name": "QUADAS-3", "url": "https://www.bristol.ac.uk/population-health-sciences/projects/quadas/quadas-3/"},
    ],
}
ROLE_LABELS = {"direct": "直接相关线索", "background": "背景 / 方法参考", "pending": "材料待确认"}
REPORT_FIELDS = {
    "study_design": ("prospective", "retrospective", "randomized", "phantom", "systematic review", "meta-analysis"),
    "data_and_sample": ("patients", "participants", "scans", "datasets", "subjects"),
    "reference_standard": ("reference standard", "ground truth", "radiologists", "histopatholog", "annotat"),
    "data_independence": ("patient-level", "independent", "external", "held-out", "hold-out", "disjoint"),
    "performance": ("sensitivity", "specificity", "auc", "froc", "false positive", "accuracy", "f1"),
    "statistical_uncertainty": ("confidence interval", "95% ci", "standard deviation", "bootstrap"),
    "external_testing": ("external test", "external validation", "multi-center", "multicenter", "cross-vendor", "cross-scanner"),
}
CONTEXT_NAMES = {"跨机型", "低剂量", "域泛化", "域适应", "域偏移", "外部验证", "精准分型", "深度学习"}


def hit(text, term):
    text, term = re.sub(r"[-–—]", " ", text.casefold()), re.sub(r"[-–—]", " ", term.casefold())
    if re.search(r"[\u4e00-\u9fff]", term):
        return term in text
    return re.search(r"(?<![a-z0-9])" + re.escape(term).replace(r"\ ", r"\s+") + r"(?![a-z0-9])", text) is not None


def assess(paper, task, groups):
    """Role in this question, with auditable text; never support/refute."""
    abstract = paper.get("abstract") or ""
    title = paper.get("title") or ""
    text = title + " " + abstract
    matched, missing = [], []
    for group in groups:
        (matched if any(hit(text, term) for term in group) else missing).append(group[0])
    context = [g[0] for g in groups if g[0] in CONTEXT_NAMES]
    missing_context = [g for g in context if g in missing]
    sentences = [s.strip() for s in re.split(r"(?<=[.!?。])\s+", abstract) if s.strip()]
    reporting = {}
    for name, terms in REPORT_FIELDS.items():
        snippets = [s for s in sentences if any(t in s.casefold() for t in terms)][:2]
        reporting[name] = {"status": "摘要有相关措辞，需核对含义" if snippets else "摘要未明确，未评估全文",
                           "quotes": snippets, "location": "abstract"}
    link = paper.get("record_url") or paper.get("url") or ""
    identifiable = bool(paper.get("doi") or paper.get("pmid") or re.match(r"https?://", link))
    demo = paper.get("source") == "mock"
    raw_type = str((paper.get("source_record") or {}).get("type") or paper.get("publication_type") or "").casefold()
    resource_only = raw_type in {"dataset", "data-set", "software", "paratext"}
    eligible = bool(title and abstract and identifiable and paper.get("year") is not None and groups and not demo and paper.get("decision") != "excluded")
    operation_mismatch = False
    modality_mismatch = bool(re.search(r"(?<![a-z])ct(?![a-z])|断层", task.get("topic", ""), re.I) and
                             re.search(r"x.ray|radiograph", title, re.I) and not re.search(r"\bCT\b|computed tomography", title, re.I))
    if "检测" in task.get("topic", "") or re.search(r"\bdetection\b", task.get("topic", ""), re.I):
        operation_mismatch = bool(re.search(r"classif|malignancy risk|risk stratifi|risk predict|risk assessment|prognos|segment", title, re.I)) and not bool(re.search(r"nodule detection|detection of .*nodul|nodules? detect", title, re.I))
    if resource_only:
        role, eligible = "background", False
        reason = "来源明确标为数据集/软件等研究资源；保留资源记录，不计入推荐论文数"
    elif modality_mismatch:
        role, eligible = "background", False
        reason = "题名研究胸片而本题指定CT；保留为邻近方法记录，不进入本题推荐清单"
    elif not eligible:
        role = "pending"
        reason = "缺少摘要、可识别题录或实际来源，尚不能据此判断与问题的关系" if not demo else "本地演示样本，不作为真实推荐文献"
    elif missing_context or operation_mismatch:
        role = "background"
        reason = ("可提供相关背景或方法；" + ("未覆盖主题要素：" + "、".join(missing_context) if missing_context else ""))
        if operation_mismatch:
            reason += "；题名偏向分类/分割/风险预测等邻近任务，不能当作完整检测验证"
    else:
        role = "direct"
        reason = "题名/摘要覆盖当前主题要素，可作为直接研究线索；不表示结论已被证明"
    relevant_quotes = sorted(sentences, key=lambda s: -sum(any(hit(s, t) for t in g) for g in groups))[:2]
    return {"role": role, "role_label": ROLE_LABELS[role], "reason": reason,
            "eligible_for_recommendation": eligible, "matched_elements": matched,
            "missing_elements": missing, "operation_mismatch": operation_mismatch, "modality_mismatch": modality_mismatch,
            "resource_only": resource_only, "source_publication_type": raw_type,
            "context_coverage": (len(context) - len(missing_context)) / len(context) if context else 1.0,
            "reporting_fields": reporting, "quotes": relevant_quotes, "location": "abstract",
            "claim_direction": "not_assessed", "certainty": "not_assessed",
            "limits": "摘要初分；字段提及含义、研究偏倚、适用范围及全文结果待核，不执行GRADE/QUADAS评分"}


def annotate(papers, task, groups):
    for paper in papers:
        paper["question_assessment"] = assess(paper, task, groups)
    return papers


def select(papers, task, groups):
    annotate(papers, task, groups)
    eligible = [p for p in papers if p["question_assessment"]["eligible_for_recommendation"]]
    eligible.sort(key=lambda p: (not (p.get('manual_review') and p['decision'] == 'included'),
                                 p["question_assessment"]["role"] != "direct",
                                 p["question_assessment"]["operation_mismatch"],
                                 -p["question_assessment"]["context_coverage"],
                                 -(p.get("relevance_score") or 0), -(p.get("year") or 0)))
    cap = min(POLICY["display_max"], max(1, int(task.get("max_papers") or POLICY["display_target"])))
    target = min(POLICY["display_target"], cap)
    chosen = eligible[:target]
    # Extra records are justified by new role/design/method coverage, not quota.
    signatures = {(p["question_assessment"]["role"], tuple(p["question_assessment"]["matched_elements"])) for p in chosen}
    for paper in eligible[target:]:
        signature = (paper["question_assessment"]["role"], tuple(paper["question_assessment"]["matched_elements"]))
        if len(chosen) >= cap:
            break
        if signature not in signatures:
            chosen.append(paper)
            signatures.add(signature)
    return chosen, {"policy_version": POLICY["version"], "eligible_pool": len(eligible),
                    "recommended_count": len(chosen), "display_target": target, "display_cap": cap,
                    "status": "推荐清单已形成" if len(chosen) >= POLICY["display_min"] else "推荐不足5篇，保留实际数量",
                    "certainty": "未评估；推荐数量不代表证据确定性", "threshold_basis": POLICY["threshold_basis"]}


def budget_decision(papers, task, groups, history):
    """An explicit heuristic stopping decision, not a systematic-review rule."""
    selected, info = select(papers, task, groups)
    covered = {x for p in selected for x in p["question_assessment"]["matched_elements"]}
    coverage = len(covered.intersection(g[0] for g in groups)) / len(groups) if groups else 0.0
    recent = history[-POLICY["low_yield_batches"]:]
    low_gain = len(recent) == POLICY["low_yield_batches"] and all(x["new_eligible_rate"] < POLICY["low_new_relevant_yield"] for x in recent)
    needs_direct = any(g[0] in CONTEXT_NAMES for g in groups)
    direct_count = sum(p['question_assessment']['role'] == 'direct' for p in selected)
    ready = len(selected) >= info["display_target"] and coverage >= POLICY["topic_coverage_stop"] and (not needs_direct or direct_count > 0)
    stop = ready or (len(selected) >= POLICY["display_min"] and low_gain)
    reason = "展示清单与主题要素覆盖达到本次预算目标" if ready else "已有可用清单，连续批次新增相关题录比例较低" if stop else "继续获取：推荐数量或主题要素仍待补充"
    observed = (sum(x["new_eligible"] for x in history) + 1) / (sum(x["returned"] for x in history) + 2)
    estimate = math.ceil(max(0, info["display_target"] - len(selected)) / max(0.01, observed))
    return {**info, "stop": stop, "reason": reason, "topic_element_coverage": round(coverage, 4),
            "direct_clue_count": direct_count, "needs_direct_context": needs_direct,
            "observed_new_eligible_rate": round(observed, 4), "estimated_additional_records": estimate,
            "coverage_meaning": "题名/摘要词组覆盖，不能当作问题已回答或查全率",
            "source_scope": "每个选择的来源至少尝试一批；最多4批/源；预算结束不等于证据充足"}


def overview(result):
    """No proposition is invented when the user supplies a topic/question."""
    items = []
    for paper in result.get("references", []):
        a = paper.get("question_assessment")
        if a is None:
            a = {"role": "pending", "reason": "历史运行未执行本版问题关系分析", "quotes": [], "limits": "尚未分析"}
        items.append({"title": paper["title"], "year": paper.get("year"), "venue": paper.get("venue"),
                      "role": a["role"], "stance": a["role"], "reason": a["reason"],
                      "snippet": " ".join(a.get("quotes", [])), "record_url": paper.get("record_url"),
                      "record_id": paper.get("record_id"), "reporting_fields": a.get("reporting_fields", {}), "limits": a["limits"]})
    counts = {role: sum(i["role"] == role for i in items) for role in ROLE_LABELS}
    return {"mode": "question_map", "title": "文献与研究问题的关系", "topic": result.get("topic"),
            "questions": result.get("review_questions", []), "labels": ROLE_LABELS, **counts,
            "items": items, "certainty": "not_assessed", "claim_direction": "not_assessed",
            "conclusion": "按当前研究主题整理直接相关线索、背景/方法参考与待确认材料。未预设支持或反对的结论；未做证据确定性评级。",
            "method_note": "关系分类依据题名与摘要；提取的措辞不等于全文方法学或主张支持核验。"}
