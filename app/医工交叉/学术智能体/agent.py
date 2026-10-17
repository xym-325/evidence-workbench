# -*- coding: utf-8 -*-
"""学术智能体 · 核心流程：任务卡解析 -> 检索 -> 筛选 -> 综述生成。

无第三方依赖（若装有 PyYAML 则优先用它解析，否则用内置 miniyaml）。
所有异常都在这里收敛成友好中文提示，调用方拿到的要么是 ok=True 的结果，
要么是 ok=False + errors 列表，页面上永远不会出现堆栈。
"""
import datetime
import re

import miniyaml
import sources

ALLOWED_SOURCES = ("openalex", "semantic_scholar", "pubmed", "mock")
TOP_FIELDS = ("topic", "problem_tree_node", "audience", "years", "language",
              "sources", "max_papers", "inclusion", "exclusion", "must_answer", "output")
OUTPUT_FIELDS = ("review_words", "require_unresolved_section", "require_methods_declaration")

# ---------------------------------------------------------------- 词表
# 中文词 -> 用于匹配的英文表达（检索结果是英文题录）。
SYN = {
    "肺结节": ("lung nodule", "pulmonary nodule", "nodule"),
    "结节": ("nodule",),
    "胸部CT": ("chest ct", "thoracic ct", "ct scan", "ct"),
    "低剂量": ("low-dose", "low dose"),
    "CT": ("ct",),
    "域偏移": ("domain shift", "distribution shift", "covariate shift"),
    "域泛化": ("domain generalization", "domain generalisation", "domain-generalization"),
    "域适应": ("domain adaptation",),
    "外部验证": ("external validation", "external test", "external evaluation"),
    "跨设备": ("cross-device", "cross-vendor", "cross-scanner", "multi-vendor", "scanner"),
    "跨机型": ("cross-scanner", "cross-vendor", "scanner", "vendor"),
    "跨中心": ("multi-center", "multicenter", "cross-center", "cross-institution",
               "multi-institution"),
    "多中心": ("multi-center", "multicenter", "multi-institution"),
    "单中心": ("single-center", "single-centre", "single center"),
    "疾病谱": ("disease spectrum", "case-mix", "case mix", "spectrum"),
    "拒识": ("abstention", "reject option", "rejection", "referral", "triage"),
    "工作流": ("workflow", "work flow"),
    "分层": ("stratified", "stratification"),
    "回顾": ("retrospective",),
    "泛化": ("generaliz", "generalis"),
    "检测": ("detection", "detect"),
    "诊断": ("diagnosis", "diagnostic"),
    "影像": ("imaging", "radiology", "radiograph", "image"),
    "骨干网络": ("backbone",),
    "公开榜": ("leaderboard", "leader board"),
    "NLP": ("nlp", "natural language"),
    "文本": ("text",),
    "分割": ("segmentation",),
    "工作量": ("workload",),
}
# 只有这些"高信号"词才允许用于排除标准（防止"影像/检测"这类通用词误杀）。
SPECIFIC_ZH = frozenset({
    "肺结节", "结节", "胸部CT", "低剂量", "CT", "域偏移", "域泛化", "域适应",
    "外部验证", "跨设备", "跨机型", "跨中心", "多中心", "单中心", "疾病谱",
    "拒识", "工作流", "骨干网络", "公开榜", "NLP", "文本", "分割",
})
SPECIFIC_EN = frozenset({"nlp", "text", "leaderboard", "backbone"})

SYNTH = [
    "综合来看，围绕「{q}」的讨论集中在数据分布差异与评价口径两方面，结论随设备、中心与人群而变化。",
    "就「{q}」而言，现有研究多在单一数据集或单中心回顾性数据上给出结论，跨机型、跨中心的可比证据仍然有限。",
    "从方法层面看，「{q}」与域偏移建模、外部验证的严谨性以及工作流指标定义紧密相关，需要统一口径后再做横向比较。",
    "值得注意的是，上述证据多为自动化指标对比，尚缺少与阅片工作量、假阳性代价挂钩的临床终点评价。",
]


# ---------------------------------------------------------------- YAML 解析
class _YamlFail(Exception):
    pass


def _friendly_yaml(exc):
    line = getattr(exc, "line", None)
    if line:
        msg = str(exc)
        return msg if msg.startswith("第") else "第 %d 行：%s" % (line, msg)
    mark = getattr(exc, "problem_mark", None)
    if mark is not None:            # PyYAML 的 Mark
        return "第 %d 行第 %d 列：%s" % (mark.line + 1, mark.column + 1,
                                         getattr(exc, "problem", str(exc)))
    msg = str(exc).strip()
    if msg and len(msg) < 120:
        return msg
    return "格式无法识别，请检查缩进、冒号与列表写法（可对照 task.example.yaml）"


def _load_yaml(text):
    try:
        import yaml as _pyyaml            # noqa: N813
        return _pyyaml.safe_load(text)
    except ImportError:
        pass
    except Exception as exc:              # noqa: BLE001
        raise _YamlFail(_friendly_yaml(exc))
    try:
        return miniyaml.parse(text)
    except Exception as exc:              # noqa: BLE001
        raise _YamlFail(_friendly_yaml(exc))


def _to_bool(value):
    if isinstance(value, bool):
        return value
    if isinstance(value, int) and value in (0, 1):
        return bool(value)
    if isinstance(value, str):
        low = value.strip().lower()
        if low in ("true", "yes", "on", "1"):
            return True
        if low in ("false", "no", "off", "0"):
            return False
    return None


def _parse_years(value):
    s = str(value).strip()
    m = re.search(r"(\d{4})\s*[-–—~～至到]\s*(\d{4})", s)
    if m:
        lo, hi = int(m.group(1)), int(m.group(2))
    elif re.fullmatch(r"\d{4}", s):
        lo = hi = int(s)
    else:
        return None
    if lo > hi or hi - lo > 100:
        return None
    return lo, hi


def _as_str_list(value):
    if isinstance(value, str):
        return [value]
    if isinstance(value, (list, tuple)):
        return [str(v) for v in value]
    return None


def parse_task(text):
    """解析并校验任务卡，返回 (task, errors)；errors 非空则 task 不可用。"""
    if not text or not str(text).strip():
        return None, ["缺少任务卡内容：请上传 YAML 文件，或把任务卡粘贴到文本框"]
    try:
        data = _load_yaml(str(text))
    except _YamlFail as exc:
        return None, ["YAML 解析失败：%s" % exc]

    if not isinstance(data, dict):
        return None, ["任务卡需要是「字段: 值」形式的 YAML（顶层应为映射），当前无法识别"]

    errors = []
    task = {}

    missing = [k for k in TOP_FIELDS if k not in ("inclusion", "exclusion") and data.get(k) in (None, "", [], {})]
    if missing:
        errors.append("缺少字段：" + "、".join(missing))

    for name in ("topic", "problem_tree_node", "audience", "language"):
        if data.get(name) is None:
            continue
        value = data.get(name)
        if not isinstance(value, str) or not value.strip():
            errors.append("字段 %s 需为非空文本，当前值：%s" % (name, repr(value)))
        else:
            task[name] = value.strip()

    if data.get("years") not in (None, ""):
        years = _parse_years(data.get("years"))
        if years is None:
            errors.append("字段 years 格式应为 2019-2026 这样的年份区间，当前值：%s"
                          % data.get("years"))
        else:
            task["years"] = years
            task["years_text"] = "%d-%d" % years

    if data.get("sources") is not None:
        items = _as_str_list(data.get("sources"))
        if items is None:
            errors.append("字段 sources 需为列表，例如 sources: [openalex, semantic_scholar, mock]")
        elif not items:
            errors.append("缺少字段：sources 不能为空")
        else:
            bad = [s.strip() for s in items if s.strip().lower() not in ALLOWED_SOURCES]
            if bad:
                errors.append("字段 sources 含本轮不支持的检索源：%s（仅支持 openalex / semantic_scholar / mock）"
                              % "、".join(bad))
            else:
                task["sources"] = [s.strip().lower() for s in items]

    if data.get("max_papers") not in (None, ""):
        raw = str(data.get("max_papers")).strip()
        n = None
        try:
            n = int(raw)
        except ValueError:
            pass
        if n is None or n <= 0 or n > 500:
            errors.append("字段 max_papers 需为 1-500 的整数，当前值：%s" % raw)
        else:
            task["max_papers"] = n

    for name in ("inclusion", "exclusion", "must_answer"):
        if name in ("inclusion", "exclusion") and data.get(name) in (None, []):
            task[name] = []
            continue
        if data.get(name) is None:
            continue
        items = _as_str_list(data.get(name))
        if items is None:
            errors.append("字段 %s 需为列表（每行用「- 」书写一条）" % name)
            continue
        items = [s.strip() for s in items if s and s.strip()]
        if not items:
            errors.append("字段 %s 不能为空" % name)
            continue
        task[name] = items

    out = data.get("output")
    if out is not None and not isinstance(out, dict):
        errors.append("字段 output 需为嵌套字段：%s"
                      % "、".join("output." + k for k in OUTPUT_FIELDS))
        out = None
    if isinstance(out, dict):
        missing_out = [k for k in OUTPUT_FIELDS if out.get(k) in (None, "")]
        if missing_out:
            errors.append("缺少字段：" + "、".join("output." + k for k in missing_out))
        if out.get("review_words") not in (None, ""):
            raw = str(out.get("review_words")).strip()
            try:
                n = int(raw)
            except ValueError:
                n = None
            if n is None or n <= 0 or n > 20000:
                errors.append("字段 output.review_words 需为 1-20000 的整数，当前值：%s" % raw)
            else:
                task["review_words"] = n
        for key in ("require_unresolved_section", "require_methods_declaration"):
            if out.get(key) is None:
                continue
            flag = _to_bool(out.get(key))
            if flag is None:
                errors.append("字段 output.%s 需为 true 或 false，当前值：%s" % (key, out.get(key)))
            else:
                task[key] = flag

    if errors:
        return None, errors
    return task, []


# ---------------------------------------------------------------- 筛选
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
    """mode='include' 用广义词表；mode='exclude' 只用高信号词，避免通用词误杀。"""
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
        if not key:
            continue
        if key in seen:
            continue
        seen.add(key)
        kept.append(p)
    return kept


def _screen(task, pool):
    lo, hi = task["years"]
    papers = _dedup(pool)
    incl_rules = [(r, _rule_keywords(r, "include")) for r in task["inclusion"]]
    excl_rules = [(r, _rule_keywords(r, "exclude")) for r in task["exclusion"]]
    skipped = [r for r, kws in incl_rules if not kws]

    included, excluded = [], []

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
            matched_incl = [r for r, kws in incl_rules
                            if not kws or any(_contains(_haystack(p), k) for k in kws)]
            if hit_excl:
                p["_reason"] = "命中排除标准：" + "；".join(hit_excl)
                exc.append(p)
            elif len(matched_incl) < threshold:
                miss = [r for r, kws in incl_rules if r not in matched_incl]
                p["_reason"] = "未满足纳入标准（%d/%d 条），缺：%s" % (
                    len(matched_incl), len(incl_rules), "；".join(miss))
                exc.append(p)
            else:
                p["_score"] = len(matched_incl)
                inc.append(p)
        return inc, exc

    included, excluded = evaluate(strict=True)
    note = ""
    if not included and papers:
        included, excluded = evaluate(strict=False)
        note = ("严格口径（全部纳入标准）下纳入 0 篇，已自动回退为宽松口径"
                "（满足半数及以上纳入标准），请结合方法学声明人工复核。")

    included.sort(key=lambda p: (-(p.get("_score") or 0), -(p.get("year") or 0)))
    adopted = included[:task["max_papers"]]
    return {
        "dedup": len(papers),
        "included": len(included),
        "excluded": len(excluded),
        "adopted": adopted,
        "excluded_papers": excluded,
        "note": note,
        "skipped_rules": skipped,
    }


# ---------------------------------------------------------------- 综述生成
def _count_words(text):
    text = text or ""
    return len(re.findall(r"[\u4e00-\u9fff]", text)) + len(re.findall(r"[A-Za-z0-9]+", text))


def _sentences(text, limit=16):
    """把摘要切成句子；过长的句子再按逗号/分号切成小块，尽量榨干可用内容。"""
    text = (text or "").strip()
    if not text:
        return []
    parts = re.split(r"(?<=[.!?])\s+|(?<=[。！？])", text)
    out = []
    for part in parts:
        part = part.strip()
        if len(part) < 15:
            continue
        if len(part) > 240:
            chunks = re.split(r"(?<=[;；,，])\s*", part)
            buf = ""
            for chunk in chunks:
                if buf and len(buf) + len(chunk) > 200:
                    if len(buf) >= 15:
                        out.append(buf)
                    buf = chunk
                else:
                    buf = (buf + chunk) if buf else chunk
            if buf and len(buf) >= 15:
                out.append(buf)
        else:
            out.append(part)
        if len(out) >= limit:
            break
    return out


def _join(parts):
    buf = ""
    for part in parts:
        if not buf:
            buf = part
        elif buf[-1] in "。！？.!?":
            buf += ("" if part[0] in "，、）)】" else " ") + part
        else:
            buf += " " + part
    return buf


def _authors_short(paper):
    authors = paper.get("authors") or []
    if not authors:
        return "佚名"
    first = authors[0]
    if len(authors) == 1:
        return first
    return "%s 等" % first


def _cite_sentence(paper, ref, used):
    sents = _sentences(paper.get("abstract"), 2)
    for s in sents:
        used.add((paper.get("title") or "", s))
    body = _join(sents) if sents else "该文献未提供摘要，此处仅采用题录信息"
    return "%s（%s）在《%s》（%s，检索源 %s）中报告：%s [%d]" % (
        _authors_short(paper),
        paper.get("year") or "年份不详",
        paper.get("title") or "无题名",
        paper.get("venue") or "—",
        paper.get("source") or "—",
        body,
        ref,
    )


def _dimensions(question, task):
    """把必答问题拆成可组织证据的子维度（模板化，但内容来自任务卡字段）。"""
    dims = ["围绕「%s」的定义与识别口径" % zh for zh in SYN if zh in question][:3]
    if task.get("inclusion"):
        dims.append("支持证据的研究设计与数据范围（纳入标准：%s）" % "；".join(task["inclusion"]))
    if task.get("exclusion"):
        dims.append("需排除的对照情形（排除标准：%s）" % "；".join(task["exclusion"]))
    if not dims:
        dims = ["研究设计与数据来源", "方法与评价指标", "结论边界与局限"]
    return dims


def _paper_card(paper, ref, question):
    """每篇采用文献的题录卡：作者/年份/载体/来源/与本问的匹配点。"""
    kws = [k for k in _rule_keywords(question, "include") if _contains(_haystack(paper), k)][:4]
    return "题录信息：《%s》[%d]，%s，%s 年，载于 %s，经 %s 检索获得；与本问的匹配点包括 %s。" % (
        paper.get("title") or "无题名",
        ref,
        "、".join(paper.get("authors") or ["佚名"]),
        paper.get("year") or "年份不详",
        paper.get("venue") or "—",
        paper.get("source") or "—",
        "、".join(kws) or "题名/摘要与本问主题接近",
    )


def _distribution(ranked, question, ref_no):
    """按数据说话的证据分布小结。"""
    years = [p.get("year") for p in ranked if p.get("year")]
    kws = _rule_keywords(question, "include")
    direct = sum(1 for p in ranked if any(_contains(_haystack(p), k) for k in kws))
    srcs = {}
    for p in ranked:
        srcs[p.get("source") or "—"] = srcs.get(p.get("source") or "—", 0) + 1
    span = ("%d-%d" % (min(years), max(years))) if years else "年份不详"
    return ("证据分布小结：与本问相关的采用文献共 %d 篇，其中关键词直接命中 %d 篇、"
            "主题相关 %d 篇；发表年份跨度 %s；来源分布为 %s；对应参考文献编号 %s。"
            % (len(ranked), direct, len(ranked) - direct, span,
               "、".join("%s %d 篇" % (k, v) for k, v in sorted(srcs.items())),
               "、".join("[%d]" % ref_no[id(p)] for p in ranked[:10]) or "无"))


def _rank(papers, question):
    kws = _rule_keywords(question, "include")
    scored = []
    for p in papers:
        hay = _haystack(p)
        score = sum(1 for k in kws if _contains(hay, k))
        scored.append((score, p))
    scored.sort(key=lambda x: (-x[0], -(x[1].get("year") or 0)))
    return [p for _, p in scored]


def _answer_paragraphs(task, question, adopted, ref_no, budget, synth_i):
    if not adopted:
        return ["（本次检索未得到符合纳入标准的文献，此处仅保留问题框架，暂无可引用的实证内容。）"]
    ranked = _rank(adopted, question)
    kws = _rule_keywords(question, "include")
    hits = sum(1 for p in ranked if any(_contains(_haystack(p), k) for k in kws))
    parts = ["针对“%s”，结合 %d 篇相关文献（其中 %d 篇与该问题关键词直接相关），可从以下几个方面作答。"
             % (question, len(ranked), hits)]
    parts.append("回答框架：%s。" % "；".join(_dimensions(question, task)))

    used = set()
    for paper in ranked:
        if len(parts) >= 6 and _count_words(_join(parts)) >= budget:
            break
        parts.append(_cite_sentence(paper, ref_no[id(paper)], used))

    # 每篇文献的题录卡（补足可核验细节，内容互不重复）
    for paper in ranked[:12]:
        if _count_words(_join(parts)) >= budget:
            break
        parts.append(_paper_card(paper, ref_no[id(paper)], question))

    # 摘要池：把所有摘要句子按相关性顺序铺开，填到目标字数为止
    pool = []
    for paper in ranked:
        for s in _sentences(paper.get("abstract"), 16):
            key = (paper.get("title") or "", s)
            if key in used:
                continue
            used.add(key)
            pool.append((paper, s))
    idx = 0
    while _count_words(_join(parts)) < budget and idx < len(pool):
        paper, sentence = pool[idx]
        idx += 1
        parts.append("另据《%s》[%d]：%s" % (
            (paper.get("title") or "无题名")[:60], ref_no[id(paper)], sentence))

    parts.append(_distribution(ranked, question, ref_no))
    parts.append(SYNTH[synth_i % len(SYNTH)].format(q=question.rstrip("？?")))

    paras, buf, words = [], [], 0
    for part in parts:
        buf.append(part)
        words += _count_words(part)
        if words >= 170:
            paras.append(_join(buf))
            buf, words = [], 0
    if buf:
        paras.append(_join(buf))
    return paras


def _build_body(task, adopted, ref_no):
    questions = task["must_answer"]
    budget = max(150, int(task["review_words"] / max(1, len(questions))))
    intro = ("本综述围绕主题「%s」，对应问题树节点「%s」，面向读者「%s」，"
             "依据最终采用的 %d 篇文献，按 %d 个必答问题逐条组织；句末方括号编号对应文末参考文献。"
             % (task["topic"], task["problem_tree_node"], task["audience"],
                len(adopted), len(questions)))
    blocks = [{"heading": "", "paragraphs": [intro]}]
    for i, question in enumerate(questions, 1):
        blocks.append({
            "heading": "%d. %s" % (i, question),
            "paragraphs": _answer_paragraphs(task, question, adopted, ref_no, budget, i - 1),
        })
    return blocks


def _build_unresolved(task, adopted):
    items = ["%s：现有文献尚无统一结论，缺乏在同一数据与同一评价口径下的直接对比。"
             % q.rstrip("？?") for q in task["must_answer"]]
    items.extend([
        "跨机型、跨剂量协议的公开基准仍不统一，域偏移的量化口径与可比性缺少共识。",
        "所纳入证据以回顾性研究为主，前瞻性、多中心外部验证不足，外推需谨慎。",
        "拒识阈值与工作流交接缺少以阅片工作量、假阳性代价为约束的联合评价。",
        "关键词自动初筛存在偏差（含中文规则与英文题录的语义鸿沟），需人工阅读复核。",
    ])
    return items[:8]


def _build_methods(task, records, screened, screen_line):
    today = datetime.date.today().isoformat()
    hit_desc = []
    for r in records:
        if r["status"] == "命中":
            hit_desc.append("%s 命中 %d 篇" % (r["source"], r["count"]))
        elif r["status"] == "未命中":
            hit_desc.append("%s 未命中" % r["source"])
        else:
            hit_desc.append("%s 请求失败（%s）" % (r["source"], r["detail"]))
    num = "①②③④⑤⑥⑦⑧⑨⑩"

    def numbered(rules):
        return " ".join("%s%s" % (num[i] if i < len(num) else "%d." % (i + 1), r)
                        for i, r in enumerate(rules))

    lines = [
        "检索源与顺序：%s；检索式：以任务卡 topic「%s」作为关键词查询，限制出版年份 %s。"
        % (" / ".join(task["sources"]), task["topic"], task["years_text"]),
        "检索时间范围：%s（任务卡指定）；检索日期：%s；单个来源请求超时 10 秒、失败最多重试 2 次。"
        % (task["years_text"], today),
        "纳入标准：%s" % numbered(task["inclusion"]),
        "排除标准：%s" % numbered(task["exclusion"]),
        "文献数量：%s；检索命中合计 %d 篇，去重后 %d 篇，%s。" % (
            "；".join(hit_desc),
            sum(r["count"] for r in records),
            screened["dedup"],
            screen_line,
        ),
        "筛选口径：先排除年份越界与命中排除标准者，再要求满足全部纳入标准；"
        "去重按题名归一化。自动关键词初筛，不能替代人工阅读全文。",
    ]
    if screened.get("note"):
        lines.append("说明：%s" % screened["note"])
    if screened.get("skipped_rules"):
        lines.append("以下纳入标准缺少可匹配关键词，本轮未参与筛选，请人工补读：%s"
                     % "；".join(screened["skipped_rules"]))
    failed = [r for r in records if r["status"] == "请求失败"]
    if failed:
        lines.append("失败来源已标注并跳过，不影响其余来源：%s"
                     % "；".join("%s（%s）" % (r["source"], r["detail"]) for r in failed))
    lines.append("生成方式：本版为模板化生成（题录 + 摘要句按必答问题结构化组织），"
                 "真正的 LLM 深度综述留待后续接入。")
    return lines


# ---------------------------------------------------------------- 总流程
def _run(task):
    records, pool = [], []
    for source in task["sources"]:
        try:
            papers = sources.search(source, task)
            status = "命中" if papers else "未命中"
            detail = ""
        except Exception as exc:  # noqa: BLE001 单源失败不中断
            papers, status, detail = [], "请求失败", sources.friendly_error(exc)
        pool.extend(papers)
        records.append({"source": source, "status": status,
                        "count": len(papers), "detail": detail})

    screened = _screen(task, pool)
    adopted = screened["adopted"]
    ref_no = {id(p): i + 1 for i, p in enumerate(adopted)}

    search_line = " | ".join(
        "%s %s%s" % (r["source"], r["status"],
                     " %d 篇" % r["count"] if r["status"] == "命中" else
                     "（%s）" % r["detail"] if r["status"] == "请求失败" else "")
        for r in records)
    screen_line = "纳入 %d 篇，排除 %d 篇，最终采用 %d 篇（≤%d 篇）" % (
        screened["included"], screened["excluded"], len(adopted), task["max_papers"])

    sections = [{
        "index": "一",
        "title": "正文综述（目标 %d 字，围绕 %d 个 must_answer 组织）"
                 % (task["review_words"], len(task["must_answer"])),
        "blocks": _build_body(task, adopted, ref_no),
    }]
    if task.get("require_unresolved_section"):
        sections.append({
            "index": "二",
            "title": "未解决 / 开放问题（require_unresolved_section）",
            "blocks": [{"heading": "", "paragraphs": []}],
            "items": _build_unresolved(task, adopted),
        })
    if task.get("require_methods_declaration"):
        sections.append({
            "index": "三",
            "title": "方法学声明（require_methods_declaration）",
            "blocks": [{"heading": "",
                        "paragraphs": _build_methods(task, records, screened, screen_line)}],
        })

    body_words = sum(
        _count_words(p)
        for block in sections[0]["blocks"]
        for p in block["paragraphs"])

    references = [{
        "n": ref_no[id(p)],
        "title": p.get("title") or "无题名",
        "authors": "、".join(p.get("authors") or []) or "佚名",
        "year": p.get("year") or "年份不详",
        "venue": p.get("venue") or "—",
        "source": p.get("source") or "—",
    } for p in adopted]

    excluded = [{
        "title": p.get("title") or "无题名",
        "year": p.get("year") or "年份不详",
        "source": p.get("source") or "—",
        "reason": p.get("_reason") or "未满足纳入标准",
    } for p in screened["excluded_papers"]]

    echo = _echo(task)
    notes = []
    if screened.get("note"):
        notes.append(screened["note"])
    for r in records:
        if r["status"] == "请求失败":
            notes.append("检索源 %s 请求失败（%s），已跳过并继续其他来源。"
                         % (r["source"], r["detail"]))
    if screened.get("skipped_rules"):
        notes.append("有纳入标准缺少可匹配关键词，未参与筛选：%s"
                     % "；".join(screened["skipped_rules"]))

    return {
        "ok": True,
        "topic": task["topic"],
        "search_line": search_line,
        "screen_line": screen_line,
        "search": records,
        "echo": echo,
        "sections": sections,
        "references": references,
        "adopted": references,
        "excluded": excluded,
        "notes": notes,
        "word_count": body_words,
        "target_words": task["review_words"],
        "stats": {"hit": sum(r["count"] for r in records),
                  "dedup": screened["dedup"],
                  "included": screened["included"],
                  "excluded": screened["excluded"],
                  "adopted": len(adopted)},
    }


def _fmt_echo_value(key, task):
    value = task.get(key)
    if key == "years":
        return task.get("years_text", "")
    if isinstance(value, list):
        return "；".join("%d) %s" % (i + 1, v) for i, v in enumerate(value))
    if key == "output":
        return ("review_words=%d；require_unresolved_section=%s；require_methods_declaration=%s"
                % (task.get("review_words", 0),
                   str(task.get("require_unresolved_section")).lower(),
                   str(task.get("require_methods_declaration")).lower()))
    return str(value)


ECHO_FIELDS = (
    ("topic", "topic（综述主题）"),
    ("problem_tree_node", "problem_tree_node（问题树节点）"),
    ("audience", "audience（读者对象）"),
    ("years", "years（年份范围）"),
    ("language", "language（语言要求）"),
    ("sources", "sources（检索源）"),
    ("max_papers", "max_papers（最大引用数）"),
    ("inclusion", "inclusion（纳入标准）"),
    ("exclusion", "exclusion（排除标准）"),
    ("must_answer", "must_answer（必答问题）"),
    ("output", "output（输出要求）"),
)


def _echo(task):
    return [{"label": label, "value": _fmt_echo_value(key, task)}
            for key, label in ECHO_FIELDS]


def run_task(text):
    """入口：任务卡 YAML 文本 -> 结果字典（ok 或 errors）。"""
    task, errors = parse_task(text)
    if errors:
        return {"ok": False, "errors": errors}
    try:
        return _run(task)
    except Exception as exc:  # noqa: BLE001 兜底：绝不把堆栈抛给页面
        import traceback
        traceback.print_exc()
        return {"ok": False,
                "errors": ["运行中断：%s（内部异常已捕获，服务可继续使用）" % exc]}
