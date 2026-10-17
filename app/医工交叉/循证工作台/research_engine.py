"""Research runs: explicit sources, conservative screening, preserved provenance.

No model service is called. Abstract extraction is a review aid, not full-text
reading or an automatic decision about whether a claim is scientifically true.
"""
import copy
import datetime
import hashlib
import json
import re
import urllib.parse
import evidence_policy


SYNONYMS = {
    "短视频": ["short video", "short-video", "short-form video", "tiktok"],
    "大学生": ["college student", "college students", "university students", "undergraduate"],
    "注意力": ["attention", "attentional"],
    "肺结节": ["lung nodule", "pulmonary nodule", "lung nodules", "pulmonary nodules"],
    "膝骨关节炎": ["knee osteoarthritis", "koa"],
    "骨关节炎": ["osteoarthritis"],
    "跨机型": ["cross-scanner", "cross-vendor", "scanner", "manufacturer", "vendor", "generalization", "generalisation", "generalizability", "domain adaptation", "multi-domain", "external validation", "reconstruction kernel"],
    "域偏移": ["domain shift", "distribution shift"],
    "外部验证": ["external validation", "external evaluation", "external test"],
    "精准分型": ["endotyping", "endotype", "endotypes", "phenotype", "phenotypes", "subtype", "subtypes", "clustering"],
    "低剂量": ["low-dose", "low dose", "ultra-low-dose", "ultralow dose"],
    "深度学习": ["deep learning", "convolutional neural"],
    "域泛化": ["domain generalization", "domain generalisation", "generalizability", "generalisation"],
    "域适应": ["domain adaptation"],
    "脓毒症": ["sepsis", "septic"],
    "睡眠": ["sleep", "insomnia"],
    "远程办公": ["remote work", "working from home", "telework"],
}
STOP = set("or and the a an of for in on with to is are does how what which study data analysis research method results evidence learning education".split())


def now():
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


def contains(text, term):
    text = re.sub(r"[-–—]", " ", text.lower())
    term = re.sub(r"[-–—]", " ", term.lower()).strip()
    if not term:
        return False
    if re.search(r"[\u4e00-\u9fff]", term):
        return term in text
    # 'learning rate' is not evidence about a student's learning.
    if term == "learning":
        text = re.sub(r"learning\s+(?:rate|rates)", "", text)
    return re.search(r"(?<![a-z0-9])" + re.escape(term).replace(r"\ ", r"\s+") + r"(?![a-z0-9])", text) is not None


def keywords(rule, agent):
    chunks = re.split(r"\s+(?:or|OR)\s+|或|、", rule)
    terms = []
    for chunk in chunks:
        chunk = chunk.strip().strip('"')
        if re.fullmatch(r"[A-Za-z][A-Za-z0-9 -]*", chunk):
            terms.append(chunk)
    for term in agent._rule_keywords(rule, "include"):
        if term not in {"or", "and"}:
            terms.append(term)
    for zh, alternatives in SYNONYMS.items():
        if zh in rule:
            terms.extend([zh] + alternatives)
    return list(dict.fromkeys(terms))


def topic_groups(topic):
    groups = []
    for zh, alternatives in SYNONYMS.items():
        if zh in topic or any(contains(topic, term) for term in alternatives):
            if zh == "骨关节炎" and "膝骨关节炎" in topic:
                continue
            groups.append([zh] + alternatives)
    if not groups:
        tokens = [t for t in re.findall(r"[A-Za-z][A-Za-z-]+", topic.lower()) if t not in STOP]
        if tokens:
            # Unmapped words are separate required concepts, not one broad OR
            # where an incidental single word makes an entire topic match.
            groups.extend([[token] for token in dict.fromkeys(tokens)])
    return groups


def topic_queries(task):
    """Build source syntax from the topic alone, never from canned questions."""
    topic = task["topic"]
    low = topic.lower()
    if "肺结节" in topic or re.search(r"\b(?:lung|pulmonary)\s+nodules?\b", low):
        core = '("Solitary Pulmonary Nodule"[MeSH Terms] OR "lung nodule*"[Title/Abstract] OR "pulmonary nodule*"[Title/Abstract])'
        query = core + ' AND ("Tomography, X-Ray Computed"[MeSH Terms] OR CT[Title/Abstract])'
        if any(x in low for x in ("泛化", "跨机型", "域偏移", "深度学习", "deep learning", "generaliz", "cross-", "domain")):
            query += ' AND ("Deep Learning"[MeSH Terms] OR "deep learning"[Title/Abstract] OR "domain adaptation"[Title/Abstract] OR generaliz*[Title/Abstract] OR "external validation"[Title/Abstract] OR reconstruction[Title/Abstract] OR "low dose"[Title/Abstract])'
        cross_domain = any(x in low for x in ('跨机型', '泛化', '域偏移', '域适应', 'generaliz', 'cross-scanner', 'cross-vendor', 'domain'))
        broad_query = 'pulmonary nodule domain generalization' if cross_domain else 'lung nodule CT'
        return {"pubmed": query, "openalex": broad_query, "semantic_scholar": broad_query}
    if "膝骨关节炎" in topic or "knee osteoarthritis" in low:
        return {"pubmed": '("Osteoarthritis, Knee"[MeSH Terms] OR "knee osteoarthritis"[Title/Abstract]) AND ("deep learning"[Title/Abstract] OR "machine learning"[Title/Abstract] OR endotyp*[Title/Abstract] OR phenotyp*[Title/Abstract])',
                "openalex": 'knee osteoarthritis deep learning', "semantic_scholar": 'knee osteoarthritis deep learning'}
    groups = topic_groups(topic)
    if groups and any(re.search(r"[\u4e00-\u9fff]", x[0]) for x in groups):
        terms = [next((t for t in g[1:] if not re.search(r"[\u4e00-\u9fff]", t)), g[0]) for g in groups]
        plain = " ".join(terms[:4])
        pubmed = " AND ".join('("%s"[Title/Abstract])' % t.replace('"', '') for t in terms[:4])
    else:
        terms = [x for x in re.findall(r"[A-Za-z][A-Za-z-]+", topic.lower()) if x not in STOP]
        plain = " ".join(dict.fromkeys(terms)) if terms else topic
        pubmed = plain
    return {"pubmed": pubmed, "openalex": plain, "semantic_scholar": plain}


def normalize(paper):
    p = copy.deepcopy(paper)
    doi = str(p.get("doi") or "").strip()
    doi = re.sub(r"^https?://(?:dx\.)?doi\.org/", "", doi, flags=re.I)
    p["doi"] = doi
    p["url"] = str(p.get("url") or "")
    p["identifier"] = doi or str(p.get("pmid") or "") or p["url"]
    p["record_url"] = ("https://doi.org/" + urllib.parse.quote(doi, safe="/")) if doi else (
        "https://pubmed.ncbi.nlm.nih.gov/%s/" % p["pmid"] if p.get("pmid") else p["url"])
    if not re.match(r"^https?://", p["record_url"], re.I):
        p["record_url"] = ""
    p["search_url"] = "https://pubmed.ncbi.nlm.nih.gov/?term=" + urllib.parse.quote(p.get("title") or "")
    key = doi.lower() or ("pmid:" + str(p["pmid"]) if p.get("pmid") else
                         re.sub(r"\W+", "", (p.get("title") or "").lower()))
    p["record_id"] = hashlib.sha256(key.encode()).hexdigest()[:24]
    p["evidence_level"] = "演示语料" if p.get("source") == "mock" else ("仅摘要级证据" if p.get("abstract") else "仅题录")
    p["fulltext_status"] = "全文待核"
    return p


def screening_capability(task, agent):
    if task.get("screening_mode", "strict") == "topic":
        return {"mode": "topic_relevance", "semantic_model": False, "uninterpreted_rules": [],
                "scope": "按用户主题宽松初筛与排序；不执行模板附加条件；不是全文或研究质量审核"}
    issues = []
    for kind in ("inclusion", "exclusion"):
        for rule in task[kind]:
            negation = kind == "exclusion" and re.search(r"无|未|不|without|\bno\b|\bnot\b", rule, re.I)
            if negation or not keywords(rule, agent):
                issues.append({"field": kind, "rule": rule,
                               "reason": "含否定或语义关系" if negation else "无法转换为当前支持的关键词条件"})
    return {"mode": "keyword_rules", "semantic_model": False, "uninterpreted_rules": issues}


def search_summary(record):
    source = record["source"]
    count = record.get("retrieved_count", record.get("count", 0))
    if record["status"] == "请求失败":
        return "%s 请求失败：%s（未取得记录，总匹配数未知）" % (source, record.get("detail", ""))
    if source == "mock":
        return "mock 演示样本 %d 条（非数据库检索）" % count
    total = record.get("total_count")
    text = "%s：本批获取 %d 条；数据库报告总匹配 %s" % (source, count, str(total) if total is not None else "未知")
    if record.get("pages"):
        text += "；每批上限50条，最多4批"
    elif record.get("request_limit") is not None:
        text += "；单批上限 %s" % record["request_limit"]
    if record.get("pages"):
        text += "；批次 " + " / ".join("第%s批%s条%s" %
            (p.get("page", i + 1), p.get("retrieved_count", 0),
             "（失败）" if p.get("error") or p.get("status") in {"failed", "请求失败"} else "")
            for i, p in enumerate(record["pages"]))
    if record.get("pagination_status") == "partial":
        text += "；部分获取，已保留成功批次：" + record.get("detail", "")
    if record.get("truncated") is True:
        text += "；未完整获取"
    elif total is None:
        text += "（不能用本批数量代替总数）"
    return text


def refresh_summary(result):
    """Also used when displaying historical runs; never backfill unrecorded totals."""
    result["search_line"] = " | ".join(search_summary(s) for s in result.get("search", []))
    result["stats"]["retrieved"] = result["stats"].get("retrieved", result["stats"].get("hit", 0))
    result["stats"]["count_basis"] = "retrieved_batch"


def screen(task, papers, agent, decisions=None):
    unique = deduplicate(papers)
    if any(record_id not in unique for record_id in (decisions or {})):
        raise ValueError("人工判定包含不属于本次检索的文献编号")
    groups = topic_groups(task["topic"])
    if task.get("screening_mode", "strict") == "topic":
        return screen_topic(task, list(unique.values()), groups, decisions)
    out = {"included": [], "uncertain": [], "excluded": []}
    for p in unique.values():
        hay = p.get("title", "") + " " + (p.get("abstract") or "")
        missing, matches, unparsed = [], [], []
        for rule in task["inclusion"]:
            terms = keywords(rule, agent)
            hits = [k for k in terms if contains(hay, k)]
            matches.append({"rule": rule, "hits": hits})
            if not terms:
                unparsed.append(rule)
            elif not hits:
                missing.append(rule)
        # Conservative: absent concepts are uncertain, never silently loosen rules.
        absent = [g[0] for g in groups if not any(contains(hay, k) for k in g)]
        excluded_by, unparsed_exclusions = [], []
        for rule in task["exclusion"]:
            # Rules containing negation require human interpretation.
            if re.search(r"无|未|不|without|\bno\b|\bnot\b", rule, re.I):
                unparsed_exclusions.append(rule)
                continue
            terms = keywords(rule, agent)
            if not terms:
                unparsed_exclusions.append(rule)
            elif any(contains(hay, k) for k in terms):
                excluded_by.append(rule)
        year = p.get("year")
        if year is not None and not task["years"][0] <= year <= task["years"][1]:
            decision, reason = "excluded", "年份不在任务时间窗"
        elif excluded_by:
            decision, reason = "excluded", "命中排除条件：" + "；".join(excluded_by)
        elif missing or absent or unparsed or unparsed_exclusions or year is None or not p.get("abstract") or not groups:
            decision = "uncertain"
            reason = "；".join(filter(None, [
                "未满足纳入条件：" + "；".join(missing) if missing else "",
                "题名/摘要未覆盖主题条件：" + "、".join(absent) if absent else "",
                "条件需人工解释：" + "；".join(unparsed) if unparsed else "",
                "排除条件需人工解释：" + "；".join(unparsed_exclusions) if unparsed_exclusions else "",
                "年份缺失" if year is None else "",
                "摘要缺失" if not p.get("abstract") else "",
                "主题尚无可可靠匹配的概念，请人工确认" if not groups else "",
            ]))
        else:
            decision, reason = "included", "主题概念和纳入关键词初筛通过；须人工核查正文"
        p["system_decision"], p["system_reason"] = decision, reason
        p["matched_rules"] = matches
        # Relevance triage and final eligibility are different decisions. An
        # uninterpreted condition must stay pending, but must not hide a record
        # that matches all currently assessable topic/inclusion conditions.
        p["topic_matches"] = [{"concept": g[0], "hits": [k for k in g if contains(hay, k)]} for g in groups]
        p["pending_criteria"] = unparsed + unparsed_exclusions
        p["shortlist_candidate"] = bool(
            decision == "uncertain" and groups and not absent and not missing and
            not excluded_by and p.get("abstract") and year is not None and
            task["years"][0] <= year <= task["years"][1] and (unparsed or unparsed_exclusions))
        override = (decisions or {}).get(p["record_id"])
        if override:
            decision, reason = override["decision"], "人工判定：" + override["reason"]
            p["manual_review"] = override
            p["shortlist_candidate"] = False
        p["decision"], p["reason"] = decision, reason
        p["eligibility_status"] = "待核候选（条件未确认）" if p["shortlist_candidate"] else (
            "人工纳入" if override and decision == "included" else "规则初筛纳入" if decision == "included" else "未纳入")
        out[decision].append(p)
    out["dedup"] = len(unique)
    return out


def deduplicate(papers):
    """Merge cross-source copies even when one source has no DOI."""
    kept = []
    for paper in papers:
        p = normalize(paper)
        title = re.sub(r"\W+", "", p.get("title", "").casefold())
        match = None
        for prior in kept:
            doi_same = p["doi"] and prior["doi"].casefold() == p["doi"].casefold()
            pmid_same = p.get("pmid") and str(prior.get("pmid")) == str(p["pmid"])
            title_same = title and title == re.sub(r"\W+", "", prior.get("title", "").casefold())
            conflicting_dois = p["doi"] and prior["doi"] and not doi_same
            if doi_same or pmid_same or (title_same and p.get("year") == prior.get("year") and not conflicting_dois):
                match = prior
                break
        if match is None:
            p["retrieved_from"] = [p.get("source", "")]
            kept.append(p)
            continue
        match["retrieved_from"] = list(dict.fromkeys(match.get("retrieved_from", []) + [p.get("source", "")]))
        for key in ("doi", "pmid", "abstract", "authors", "venue", "url"):
            if not match.get(key) and p.get(key):
                match[key] = p[key]
        match.update(normalize(match))
    return {p["record_id"]: p for p in kept}


def screen_topic(task, papers, groups, decisions=None):
    """Relaxed topic triage: supplementary concepts rank, not veto records."""
    primary = [g for g in groups if g[0] in {"肺结节", "膝骨关节炎", "骨关节炎", "短视频", "脓毒症", "远程办公", "睡眠"}]
    out = {"included": [], "uncertain": [], "excluded": [], "dedup": len(papers)}
    for p in papers:
        hay = p.get("title", "") + " " + (p.get("abstract") or "")
        matches = [{"concept": g[0], "hits": [k for k in g if contains(hay, k)]} for g in groups]
        hit_count = sum(bool(m["hits"]) for m in matches)
        ranking_bonus = 0
        if any(g[0] in {"跨机型", "域泛化", "域适应", "域偏移"} for g in groups):
            ranking_bonus += 5 if any(contains(hay, t) for t in ("cross-scanner", "cross-vendor", "multi-vendor", "domain adaptation", "domain generalization", "multi-domain", "external validation", "generalizability", "reconstruction kernel")) else 0
        if any(g[0] == "低剂量" for g in groups):
            ranking_bonus += 2 if any(contains(hay, t) for t in SYNONYMS["低剂量"]) else 0
        primary_hit = any(any(contains(hay, k) for k in g) for g in primary)
        related = primary_hit if primary else hit_count >= min(2, len(groups)) if groups else False
        year = p.get("year")
        if year is not None and not task["years"][0] <= year <= task["years"][1]:
            decision, reason = "excluded", "年份不在用户指定时间窗"
        elif related:
            decision, reason = "included", "主题相关初筛通过：" + "、".join(m["concept"] for m in matches if m["hits"]) + "；补充概念仅用于排序，未附加影像/外部验证等要求"
        else:
            decision, reason = "uncertain", "已保留检索题录，题名/摘要的主题匹配不足，供进一步查看"
        p.update(system_decision=decision, system_reason=reason, decision=decision, reason=reason,
                 relevance_score=hit_count + ranking_bonus, topic_matches=matches, matched_rules=[], pending_criteria=[],
                 shortlist_candidate=bool(not groups and decision == "uncertain"),
                 eligibility_status="主题相关初筛（待人工核查）" if decision == "included" else "主题匹配待确认")
        override = (decisions or {}).get(p["record_id"])
        if override:
            decision = override["decision"]
            p.update(decision=decision, reason="人工判定：" + override["reason"], manual_review=override,
                     shortlist_candidate=False, eligibility_status="人工" + decision)
        out[decision].append(p)
    return out


def reference(p, n):
    r = copy.deepcopy(p)
    r["n"] = n
    r["authors_list"] = p.get("authors") or []
    r["authors"] = "、".join(p.get("authors") or [])
    r["author"] = (p.get("authors") or [""])[0]
    return r


def build_result(task, papers, search, agent, decisions=None):
    screened = screen(task, papers, agent, decisions)
    by_year = lambda p: (-(p.get("relevance_score") or 0), -(p.get("year") or 0))
    strict = sorted(screened["included"], key=by_year)[:task["max_papers"]]
    candidates = sorted([p for p in screened["uncertain"] if p.get("shortlist_candidate")], key=by_year)
    provisional = candidates[:max(0, task["max_papers"] - len(strict))]
    adopted = strict + provisional
    selection = {}
    if task.get("screening_mode") == "topic":
        adopted, selection = evidence_policy.select(screened["included"] + candidates, task, topic_groups(task["topic"]))
        strict = [p for p in adopted if p["decision"] == "included"]
        provisional = [p for p in adopted if p["decision"] != "included"]
    else:
        adopted = adopted[:evidence_policy.POLICY["display_max"]]
        strict = [p for p in adopted if p["decision"] == "included"]
        provisional = [p for p in adopted if p["decision"] != "included"]
    refs = [reference(p, n) for n, p in enumerate(adopted, 1)]
    blocks = [{"heading": "", "paragraphs": [
        "本稿为摘要级材料初稿；未自动阅读或核验全文。按必答问题组织原文摘要，结论须人工确认。"
        + (" 其中 %d 条为待核候选，纳入条件未确认；允许继续核验题录，不能作为已纳入证据。" % len(provisional) if provisional else "")
        + (" 当前为 mock 演示，不能用于学术引用。" if "mock" in task["sources"] else "")]}]
    evidence = []
    if task.get("screening_mode") == "topic" and refs:
        blocks.append({"heading": "主题相关文献与摘要", "paragraphs": [
            "[%d] %s（%s）\n选择理由：%s\n摘要原文：%s" %
            (r["n"], r["title"], r.get("year") or "年份待核", r["reason"],
             r.get("abstract") or "该来源未返回摘要，可打开文献记录查看。") for r in refs]})
        evidence.extend({"question": task["topic"], "record_id": r["record_id"], "reference": r["n"],
                         "quote": r["abstract"], "location": "abstract", "record_url": r["record_url"],
                         "support_status": "主题相关初筛；主张待核", "eligibility_status": r["eligibility_status"]}
                        for r in refs if r.get("abstract"))
    for question in task["must_answer"]:
        qterms = keywords(question, agent)
        relevant = [r for r in refs if any(contains(r.get("abstract", ""), k) for k in qterms)]
        paragraphs = []
        for r in relevant:
            # Preserve exact sentences; do not fill a requested word count with invented synthesis.
            sentences = agent._sentences(r.get("abstract"), 16)
            selected = [s for s in sentences if any(contains(s, k) for k in qterms)]
            for sentence in selected[:2]:
                paragraphs.append("摘要原文：%s [%d]（仅摘要级，尚未验证该句是否回答完整问题）" % (sentence, r["n"]))
                evidence.append({"question": question, "record_id": r["record_id"], "reference": r["n"],
                                 "quote": sentence, "location": "abstract", "record_url": r["record_url"],
                                 "support_status": "待人工确认", "eligibility_status": r["eligibility_status"]})
        if not paragraphs:
            paragraphs = ["现有材料不足以自动回答本问题。请人工补充并确认对应证据，系统不生成替代结论。"]
        blocks.append({"heading": question, "paragraphs": paragraphs})
    # Always expose candidate abstracts even if rule keywords cannot map the
    # user's questions. This is source material, never a generated answer.
    if provisional:
        blocks.append({"heading": "待核候选材料（不计为已纳入证据）", "paragraphs": [
            "[%d] %s\n摘要原文：%s\n待确认条件：%s" %
            (r["n"], r["title"], r.get("abstract", ""), "；".join(r.get("pending_criteria", [])))
            for r in refs if r.get("shortlist_candidate")]})
    sections = [{"index": "一", "title": "摘要材料稿（含待核候选）" if provisional else "正文综述（摘要级证据初稿）", "blocks": blocks}]
    if task.get("require_unresolved_section"):
        sections.append({"index": "二", "title": "未解决 / 开放问题", "blocks": [],
                         "items": ["摘要不足以完成判断的问题、全文适用条件和证据支持关系均需人工复核。"]})
    if task.get("require_methods_declaration"):
        sections.append({"index": "三", "title": "方法学声明", "blocks": [], "items": [
            "任务主题：" + task["topic"], "时间窗：" + task["years_text"],
            "检索来源：" + "、".join(task["sources"]),
            "纳入标准：" + "；".join(task["inclusion"]), "排除标准：" + "；".join(task["exclusion"]),
            ("按用户主题宽松初筛，补充概念用于排序；未执行模板附加的影像/外部验证等限制。" if task.get("screening_mode") == "topic" else "按任务卡规则初筛；未自动放宽条件。") + "未接入大模型；全文均待核。"]})
    stats = {"hit": len(papers), "dedup": screened["dedup"], "included": len(screened["included"]),
             "uncertain": len(screened["uncertain"]), "excluded": len(screened["excluded"]), "adopted": len(refs),
             "shortlisted": len(candidates), "provisional_adopted": len(provisional), "strict_adopted": len(strict)}
    capability = screening_capability(task, agent)
    notes = []
    if capability["uninterpreted_rules"]:
        notes.append("自动筛选受限：%d 条条件不能由当前关键词规则解释，相关记录需人工判定；存疑不等于不相关。" % len(capability["uninterpreted_rules"]))
    if provisional:
        notes.append("%d 条主题关键词匹配的待核候选已进入材料稿和题录核验列表；其条件、结论支持关系均未确认，不计入初筛纳入数。" % len(provisional))
    if selection:
        notes.append("%s：%d篇；其余候选保留在筛选表。推荐数量不代表医学证据确定性。" % (selection["status"], selection["recommended_count"]))
    result = {"ok": True, "topic": task["topic"], "review_questions": task["must_answer"],
            "selection_summary": selection, "evidence_policy": evidence_policy.POLICY,
            "screening_mode": task.get("screening_mode", "strict"), "sections": sections, "references": refs,
            "adopted": refs, "screening": screened, "evidence_units": evidence,
            "excluded": screened["excluded"] + [dict(p, reason=("待核候选，可核验题录：" if p.get("shortlist_candidate") else "其他存疑：") + p["reason"]) for p in screened["uncertain"]],
            "stats": stats, "search": search, "echo": agent._echo(task), "notes": notes,
            "screening_capability": capability,
            "screen_line": "初筛纳入 %d，存疑 %d（其中待核候选 %d），排除 %d；材料稿 %d（待核候选 %d），全文待核" %
                           (stats["included"], stats["uncertain"], stats["shortlisted"], stats["excluded"], stats["adopted"], stats["provisional_adopted"]),
            "word_count": sum(agent._count_words(p) for b in blocks for p in b["paragraphs"]),
            "target_words": task["review_words"], "demo": task["sources"] == ["mock"]}
    refresh_summary(result)
    return result


def retrieve(task, sources, corpus, mesh_query, query_en, explicit_queries=None, progress=None):
    records, papers = [], []
    planned = topic_queries(task)
    seen_eligible, budget_history = set(), []
    for source in task["sources"]:
        # A naturally exhausted source may not invoke the policy callback.
        # Seed its records so the next source cannot count old records as new.
        if papers and task.get('screening_mode') == 'topic':
            previous = screen(task, papers, None)['included']
            evidence_policy.annotate(previous, task, topic_groups(task['topic']))
            seen_eligible.update(re.sub(r'\W+', '', p.get('title', '').casefold()) + ':' + str(p.get('year'))
                                 for p in previous if p['question_assessment']['eligible_for_recommendation'])
        if progress:
            progress({'stage': 'retrieval', 'source': source, 'message': '开始查询 ' + source})
        query = (explicit_queries or {}).get(source) or (mesh_query if source == "pubmed" else "") or planned.get(source) or query_en or task["topic"]
        origin = "explicit" if (explicit_queries or {}).get(source) else "selected_mesh" if source == "pubmed" and mesh_query else "planned_keywords"
        record = {"source": source, "query": query, "query_origin": origin,
                  "years": task["years_text"], "retrieved_at": now()}
        source_history = []
        def on_page(cumulative, page):
            candidates = screen(task, papers + cumulative, None)["included"]
            groups = topic_groups(task["topic"])
            evidence_policy.annotate(candidates, task, groups)
            eligible = [p for p in candidates if p["question_assessment"]["eligible_for_recommendation"]]
            keys = {re.sub(r"\W+", "", p.get("title", "").casefold()) + ':' + str(p.get("year")) for p in eligible}
            new_count = len(keys - seen_eligible)
            seen_eligible.update(keys)
            returned = page.get("retrieved_count", 0)
            entry = {"source": source, "page": page.get("page"), "returned": returned,
                     "new_eligible": new_count, "new_eligible_rate": round(new_count / returned, 4) if returned else 0.0}
            budget_history.append(entry)
            decision = evidence_policy.budget_decision(candidates, task, groups, budget_history)
            source_history.append({**entry, "decision": decision})
            if progress:
                progress({'stage': 'retrieval', 'source': source, 'page': page.get('page'),
                          'returned': returned, 'new_eligible': new_count,
                          'message': '%s 第%s批取回%s条，新增可用线索%s条' % (source, page.get('page'), returned, new_count)})
            return decision["reason"] if decision["stop"] else None
        try:
            if source == "mock":
                got = copy.deepcopy(corpus)
            else:
                got = sources.search_paginated(source, dict(task, topic=query), page_size=50, max_pages=4, query=query,
                                               on_page=on_page if task.get("screening_mode") == "topic" else None)
            total = getattr(got, "total", None)
            limit = getattr(got, "limit", None)
            truncated = total > len(got) if total is not None else (True if limit and len(got) >= limit else None)
            pagination_status = getattr(got, "status", "complete")
            page_errors = getattr(got, "errors", [])
            record.update(status="部分获取" if pagination_status == "partial" else "请求失败" if pagination_status == "failed" else "已获取" if got else "未命中" if total == 0 else "未获取记录",
                          count=len(got), retrieved_count=len(got), total_count=total, request_limit=50 if getattr(got, "pages", []) else limit,
                          truncated=truncated, request_url=getattr(got, "request_url", ""),
                          response_meta=getattr(got, "response_meta", {}),
                          pages=getattr(got, "pages", []), pagination_status=pagination_status,
                          page_size=50, max_pages=4, retrieval_budget=200,
                          adaptive_batches=source_history,
                          stop_reason=getattr(got, "response_meta", {}).get("stop_reason") or
                              ("数据库结果已取完" if getattr(got, "exhausted", False) else "本来源批次预算已结束"),
                          detail="；".join(str(x.get("error", x.get("detail", x))) if isinstance(x, dict) else str(x) for x in page_errors), records=got)
            papers.extend(got)
        except Exception as exc:
            record.update(status="请求失败", count=0, retrieved_count=0, total_count=None,
                          detail=sources.friendly_error(exc), records=[])
        records.append(record)
        if progress:
            progress({'stage': 'retrieval', 'source': source, 'message': '%s %s，实际获取%s条' % (source, record['status'], record['count'])})
    return records, papers
