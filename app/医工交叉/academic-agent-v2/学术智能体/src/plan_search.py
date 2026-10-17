# -*- coding: utf-8 -*-
"""plan_search：根据任务卡生成中英文检索式与检索计划（可复查）。

英文检索式由「领域词表」把中文关键词翻译成英文表达；无法翻译时保底使用
原主题中的拉丁词，并在计划中显式标注「英文检索式可能不完整」，绝不假装已翻译。
"""
import re

# 中文关键词 -> 英文表达（与 agent 侧筛选共享，便于统一调整）
SYN = {
    "肺结节": ("lung nodule", "pulmonary nodule"),
    "结节": ("nodule",),
    "肺癌": ("lung cancer", "lung carcinoma"),
    "胸部CT": ("chest ct", "thoracic ct"),
    "低剂量": ("low-dose", "low dose"),
    "CT": ("ct",),
    "筛查": ("screening",),
    "域偏移": ("domain shift", "distribution shift", "covariate shift"),
    "域泛化": ("domain generalization", "domain generalisation"),
    "域适应": ("domain adaptation",),
    "外部验证": ("external validation",),
    "跨设备": ("cross-device", "cross-vendor", "cross-scanner", "multi-vendor"),
    "跨机型": ("cross-scanner", "cross-vendor", "scanner"),
    "跨中心": ("multi-center", "multicenter", "cross-center"),
    "多中心": ("multi-center", "multicenter"),
    "单中心": ("single-center", "single-centre"),
    "疾病谱": ("disease spectrum", "case-mix", "case mix"),
    "拒识": ("abstention", "reject option", "referral", "triage"),
    "工作流": ("workflow", "work flow"),
    "校准": ("calibration",),
    "公平": ("fairness", "equity"),
    "解释": ("interpretab", "explainab"),
    "原因": ("causal", "causality"),
    "因果": ("causal", "causality"),
    "泛化": ("generaliz", "generalis"),
    "检测": ("detection", "detect"),
    "诊断": ("diagnosis", "diagnostic"),
    "影像": ("imaging", "radiology", "radiograph"),
    "分割": ("segmentation",),
    "脓毒症": ("sepsis", "septic"),
    "预警": ("early warning", "alert", "prediction"),
    "再入院": ("readmission",),
    "病历": ("electronic health record", "clinical note", "ehr"),
    "文本": ("clinical text", "natural language"),
    "知识图谱": ("knowledge graph",),
    "药物": ("drug", "molecul", "compound"),
    "分子": ("molecul",),
    "信号": ("signal", "waveform"),
    "心电": ("ecg", "electrocardiogram"),
    "脑电": ("eeg", "electroencephalogram"),
    "联邦": ("federated",),
    "隐私": ("privacy", "differential privacy"),
    "标注": ("annotation", "labeling", "labelling"),
    "漂移": ("drift", "shift"),
}

STOP_EN = frozenset({
    "ct", "the", "and", "for", "with", "of", "in", "on", "a", "an", "to", "ai",
})


def _latin_words(text):
    return [w.lower() for w in re.findall(r"[A-Za-z][A-Za-z0-9\-]{1,}", text or "")]


def build_query_zh(task):
    """中文检索式：主题 + 必答问题的高信号关键词。"""
    parts = [str(task.get("topic") or "").strip()]
    for q in task.get("must_answer") or []:
        q = str(q).strip("？?。. ")
        if q and q not in parts:
            parts.append(q)
    return " ".join(p for p in parts if p)


def build_terms_en(task):
    """从主题与必答问题中抽取并翻译英文检索词，去重排序。"""
    blob = " ".join([str(task.get("topic") or "")] + [str(x) for x in task.get("must_answer") or []])
    terms = []
    for zh, ens in SYN.items():
        if zh in blob:
            terms.extend(ens)
    terms.extend(_latin_words(task.get("topic") or ""))
    seen, out = set(), []
    for t in terms:
        t = t.strip().lower()
        if not t or t in seen or t in STOP_EN:
            continue
        seen.add(t)
        out.append(t)
    return out


def build_query_en(task):
    terms = build_terms_en(task)
    return " ".join(terms) if terms else ""


def plan(task):
    zh = build_query_zh(task)
    terms = build_terms_en(task)
    en = " ".join(terms)
    notes = []
    if not en:
        notes.append("未能从主题/子问题中提取英文检索词：semantic_scholar 等英文源命中率会偏低，"
                     "请在任务卡 topic 中补充英文关键词，或仅使用 mock 源。")
    if len(terms) <= 2:
        notes.append("英文检索词较少，建议在任务卡中加入领域同义词以提高召回。")
    sources = [str(s) for s in (task.get("sources") or [])]
    return {
        "topic": task.get("topic", ""),
        "years": task.get("years", ""),
        "sources": sources,
        "query_zh": zh,
        "query_en": en,
        "terms_en": terms,
        "plan": [
            "1) 中文检索式：%s" % zh,
            "2) 英文检索式：%s" % (en or "（空：见下方提示）"),
            "3) 时间范围：%s" % task.get("years", ""),
            "4) 检索源与顺序：%s" % " / ".join(sources),
            "5) 每源单次请求超时 10 秒、失败最多重试 2 次，失败源标注后继续。",
        ],
        "notes": notes,
    }


def plan_text(p):
    lines = ["【检索计划】", "综述主题：%s" % p["topic"]]
    lines.extend("  " + x for x in p["plan"])
    if p["notes"]:
        lines.append("  提示：")
        lines.extend("    - " + n for n in p["notes"])
    return "\n".join(lines)
