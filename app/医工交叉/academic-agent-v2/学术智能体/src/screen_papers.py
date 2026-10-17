# -*- coding: utf-8 -*-
"""screen_papers：题名去重 -> 年份范围 -> 排除标准 -> 纳入标准，产出纳入/排除表。

关键词匹配只用于「初筛」，任何纳入/排除都会写明原因，供人工复核；
命中排除标准优先于纳入标准；严格口径 0 篇时回退宽松口径并标注。
"""
import re

from .parse_request import _parse_years
from .plan_search import SYN

# 只有高信号词才允许用于排除标准，避免"影像/检测"这类通用词误杀
SPECIFIC_ZH = frozenset({
    "肺结节", "结节", "肺癌", "胸部CT", "低剂量", "CT", "域偏移", "域泛化", "域适应",
    "外部验证", "跨设备", "跨机型", "跨中心", "多中心", "单中心", "疾病谱", "拒识",
    "工作流", "联邦", "隐私", "知识图谱", "心电", "脑电", "脓毒症",
})
SPECIFIC_EN = frozenset({"privacy", "federated", "leaderboard", "backbone"})


def _haystack(paper):
    parts = [paper.get("title") or "", paper.get("abstract") or "", paper.get("venue") or ""]
    parts.extend(paper.get("authors") or [])
    return re.sub(r"\s+", " ", " ".join(parts)).lower()


def _contains(hay, keyword):
    kw = keyword.lower().strip()
    if not kw:
        return False
    if re.fullmatch(r"[a-z0-9][a-z0-9 \-]*", kw):
        body = re.escape(kw).replace(r"\ ", r"[\s\-]+").replace(r"\-", r"[\- ]?")
        try:
            return re.search(r"(?<![a-z0-9])" + body + r"(?![a-z0-9])", hay) is not None
        except re.error:
            return kw in hay
    return kw in hay


def _rule_keywords(rule, mode):
    kws = []
    for zh, ens in SYN.items():
        if zh in rule and (mode == "include" or zh in SPECIFIC_ZH):
            kws.extend(ens)
    for en in re.findall(r"[A-Za-z][A-Za-z0-9\-]{1,}", rule):
        low = en.lower()
        if mode == "include" or low in SPECIFIC_EN:
            kws.append(low)
    seen, out = set(), []
    for k in kws:
        if k and k not in seen:
            seen.add(k)
            out.append(k)
    return out


def _dedup(papers):
    kept, seen = [], set()
    for p in papers:
        key = re.sub(r"[^a-z0-9\u4e00-\u9fff]+", "", (p.get("title") or "").lower())
        if not key or key in seen:
            continue
        seen.add(key)
        kept.append(p)
    return kept


def screen(task, pool):
    lo, hi = _parse_years(task.get("years"))
    papers = _dedup(pool)
    incl_rules = [(r, _rule_keywords(r, "include")) for r in task.get("inclusion") or []]
    excl_rules = [(r, _rule_keywords(r, "exclude")) for r in task.get("exclusion") or []]
    skipped = [r for r, kws in incl_rules if not kws]

    def evaluate(strict):
        inc, exc = [], []
        threshold = len(incl_rules) if strict else max(1, (len(incl_rules) + 1) // 2)
        for p in papers:
            year = p.get("year")
            if year and not (lo <= year <= hi):
                p["_reason"] = "年份 %d 不在范围 %d-%d" % (year, lo, hi)
                exc.append(p)
                continue
            hit_excl = [r for r, kws in excl_rules
                        if kws and any(_contains(_haystack(p), k) for k in kws)]
            matched = [r for r, kws in incl_rules
                       if not kws or any(_contains(_haystack(p), k) for k in kws)]
            if hit_excl:
                p["_reason"] = "命中排除标准：" + "；".join(hit_excl)
                exc.append(p)
            elif len(matched) < threshold:
                miss = [r for r, kws in incl_rules if r not in matched]
                p["_reason"] = "未满足纳入标准（%d/%d 条），缺：%s" % (
                    len(matched), len(incl_rules), "；".join(miss))
                exc.append(p)
            else:
                p["_score"] = len(matched)
                inc.append(p)
        return inc, exc

    included, excluded = evaluate(strict=True)
    note = ""
    if not included and papers:
        included, excluded = evaluate(strict=False)
        note = ("严格口径（全部纳入标准）下纳入 0 篇，已自动回退为宽松口径"
                "（满足半数及以上纳入标准），请人工复核。")
    included.sort(key=lambda p: (-(p.get("_score") or 0), -(p.get("year") or 0)))
    adopted = included[:int(task.get("max_papers") or 25)]
    return {
        "dedup": len(papers),
        "included": included,
        "excluded": excluded,
        "adopted": adopted,
        "note": note,
        "skipped_rules": skipped,
    }


def identifier(paper):
    return paper.get("doi") or paper.get("url") or ""


def table_rows(papers, with_reason=False):
    rows = []
    for p in papers:
        row = {
            "title": p.get("title") or "",
            "year": p.get("year") or "",
            "venue": p.get("venue") or "",
            "source": p.get("source") or "",
            "identifier": identifier(p),
        }
        if with_reason:
            row["reason"] = p.get("_reason") or p.get("_score") and "满足纳入标准" or "未满足纳入标准"
        rows.append(row)
    return rows


def to_csv(rows, columns=("title", "year", "venue", "source", "identifier", "reason")):
    import csv
    import io
    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=list(columns), extrasaction="ignore")
    writer.writeheader()
    for r in rows:
        writer.writerow(r)
    return buf.getvalue()
