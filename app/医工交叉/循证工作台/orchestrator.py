# -*- coding: utf-8 -*-
"""循证科研智能体工作台 · 编排层

连接科研组件；任务、题录、证据和核验结果按运行编号隔离：

  第1步 检索式生成  -> 复用 mesh-query（本地 MeSH 词表，离线可用）
  第2步 检索/筛选/综述 -> research_engine（复用任务解析和检索适配）
  第3步 引用核验    -> Crossref + PubMed（失败保留待核，不回退演示缓存）
  第4步 精读引擎    -> 复用 sq3r-academic-reading 技能（读取 SKILL.md）

每个步骤都可单独调用，也可用 run_all() 一次跑通；任何异常都收敛为
{'ok': False, 'error': 友好中文提示}，绝不向页面抛堆栈。
"""
from __future__ import annotations

import json
import os
import re
import sys
import time
import datetime
import urllib.error
import urllib.parse
import urllib.request

import demo_data
import research_engine as engine
import evidence_policy
import run_store
import threading
import runtime_paths

RUN_LOCK = threading.RLock()

HERE = str(runtime_paths.workbench_root())
ROOT = str(runtime_paths.project_root())
MESH_DIR = os.path.join(ROOT, "mesh-query")
AGENT_DIR = os.path.join(ROOT, "学术智能体")
SKILL_MD = os.path.join(ROOT, "sq3r-skill", "skill", "SKILL.md")

UA = "EvidenceWorkbench/0.1 (+python-urllib)"
HTTP_TIMEOUT = 6


# ---------------------------------------------------------------- import 复用
def _ensure_paths():
    for p in (MESH_DIR, AGENT_DIR):
        if p not in sys.path:
            sys.path.append(p)


# ================================================================ 第1步
# 这些概念单独 AND 太泛，默认不勾选（可由用户手动勾上）
GENERIC = {"data", "precision", "classification", "study", "analysis",
           "method", "methods", "results", "model", "models", "system",
           "approach", "review", "clinical", "imaging", "diagnosis"}


def _clinical_concepts(text, lex):
    """Known clinical phrases are mapped as phrases, checked against local NLM."""
    low = text.casefold()
    definitions = []
    if '肺结节' in text or re.search(r'\b(lung|pulmonary)\s+nodules?\b', low):
        definitions.append(('肺结节 / pulmonary nodule', 'Solitary Pulmonary Nodule', ['lung nodule*', 'pulmonary nodule*'], False))
    elif '膝骨关节炎' in text or 'knee osteoarthritis' in low:
        definitions.append(('膝骨关节炎 / knee osteoarthritis', 'Osteoarthritis, Knee', ['knee osteoarthritis'], False))
    elif '脓毒症' in text or re.search(r'\bsepsis\b', low):
        definitions.append(('脓毒症 / sepsis', 'Sepsis', ['sepsis', 'septic'], False))
    if not definitions:
        return []
    if re.search(r'(?<![a-z])ct(?![a-z])|computed tomography|断层', low):
        definitions.append(('CT / 计算机断层成像', 'Tomography, X-Ray Computed', ['CT', 'computed tomography'], False))
    if '深度学习' in text or 'deep learning' in low:
        definitions.append(('深度学习 / deep learning', 'Deep Learning', ['deep learning'], False))
    if '低剂量' in text or re.search(r'low[ -]dose', low):
        definitions.append(('低剂量 / low-dose', '', ['low dose', 'low-dose', 'ultra-low-dose'], True))
    if any(s in low for s in ('跨机型', '域泛化', 'generaliz', 'cross-scanner', 'cross-vendor', 'scanner manufacturers')):
        definitions.append(('跨机型 / 泛化', '', ['cross-scanner', 'cross-vendor', 'domain adaptation', 'domain generalization', 'generalizability', 'external validation'], True))
    if any(s in low for s in ('精准分型', 'endotyp', 'subtyp')):
        definitions.append(('分型 / endotyping', '', ['endotyp*', 'phenotyp*', 'subtyp*'], True))
    out = []
    for label, name, terms, optional in definitions:
        exact = next((c for c in lex.candidates(name) if c['basis'] in {'exact', 'variant'} and c['name'] == name), None) if name else None
        parts = ['"%s"[MeSH Terms]' % exact['name']] if exact else []
        parts.extend('"%s"[Title/Abstract]' % term for term in terms)
        out.append({'text': label, 'status': 'matched' if exact else 'unmatched', 'mesh': exact['name'] if exact else '',
                    'ui': exact['ui'] if exact else '', 'basis': '临床短语映射；正式词已在本地NLM词表核对' if exact else '仅自由词；不编造MeSH主题词',
                    'note': '扩展概念默认不加AND，可按范围勾选' if optional else '保留完整临床概念，避免逐词误配',
                    'clause': '(' + ' OR '.join(parts) + ')', 'matched': bool(exact), 'generic': optional})
    return out


def mesh_step(pico: str) -> dict:
    """复用 mesh-query：自然语言 -> 可粘贴 PubMed 的 MeSH 检索式。

    额外给出**每个概念单独的检索子句**，供页面勾选/精简后重建检索式，
    避免"把整个 PICO 问题所有概念全部 AND"导致 0 命中。
    """
    try:
        _ensure_paths()
        from mesh_query import pipeline
        from mesh_query.compose import ComposeOptions
        from mesh_query.lexicon import Lexicon

        options = ComposeOptions(auto_subhead=False, free_syn=1)
        db = os.path.join(MESH_DIR, "data", "mesh.db")
        with Lexicon(db) as lex:
            result = pipeline.analyze(pico, lex, top=3, compose_options=options)
            clinical_chunks = _clinical_concepts(pico, lex)

        per_chunk = (result.get("compose") or {}).get("per_chunk") or []
        chunks = []
        for i, c in enumerate(result["chunks"]):
            chosen = c.get("chosen") or {}
            comp = [per_chunk[i]] if i < len(per_chunk) else []
            clause = pipeline.build_query([c], comp, options)
            name = (chosen.get("name") or "").strip()
            token = (c.get("text") or "").strip().lower()
            matched = c["status"] == "matched"
            generic = (not matched) or (name.lower() in GENERIC) or (token in GENERIC)
            chunks.append({
                "text": c["text"],
                "status": c["status"],
                "mesh": name,
                "ui": chosen.get("ui") or "",
                "basis": chosen.get("basis_label") or "",
                "note": c.get("note") or "",
                "clause": clause,
                "matched": matched,
                "generic": generic,
            })
        if clinical_chunks:
            chunks = clinical_chunks
        return {
            "ok": True,
            "query": '\n  AND '.join(c['clause'] for c in chunks if c['matched'] and not c['generic']) if clinical_chunks else result["query"],
            "input_type": result["input_type"],
            "mesh_year": result["mesh_year"],
            "chunks": chunks,
            "skipped": result["skipped"],
        }
    except FileNotFoundError as exc:
        return {"ok": False, "error": "MeSH 词表索引缺失：%s" % exc}
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": "检索式生成失败：%s" % exc}


# 演示用：把「学术智能体」模板里偏肺结节语境的措辞改写为 KOA 语境
# （仅在演示层做字符串替换，不改动原项目文件）





# 非医学示例：把影像专用措辞改成通用措辞


# ------------------------------------------------ 研究主题界定（任务卡草稿）
def draft_step(pico: str) -> dict:
    """一句话/一段要求 -> 结构化任务卡草稿 + 需人工确认项（先界定范围，再检索）。

    复用 2/学术智能体/src/parse_request.py，避免"还没界定范围就写综述"。
    """
    try:
        mod = _load_module(V2_PARSE, "wb_parse_request")
        topic = mod._extract_topic(pico)
        # The legacy fallback truncated arbitrary requests at 40 characters,
        # including in the middle of English words. Preserve the user's scope.
        if len(pico.strip()) > 40 and topic == pico.strip()[:40]:
            topic = pico.strip()
        medical = any(word in topic.lower() for word in ("肺", "ct", "nodule", "osteoarthritis", "骨关节", "sepsis", "脓毒症", "医学", "clinical", "cancer", "肿瘤", "药物"))
        task = mod.build_task(pico, topic=topic, sources=["pubmed", "openalex"] if medical else ["openalex", "semantic_scholar"])
        task["inclusion"], task["exclusion"] = [], []
        task["max_papers"] = evidence_policy.POLICY["display_target"]
        yaml_text = mod.to_yaml(task)
        yaml_text = re.sub(r"(?m)^(inclusion|exclusion):\s*$", r"\1: []", yaml_text)
        yaml_text += "\nscreening_mode: topic\n"
        confirms = [
            {"field": "主题 topic", "value": task.get("topic") or "（未识别）",
             "note": "由你的问题自动提炼，请确认范围是否准确、是否过宽/过窄"},
            {"field": "时间窗 years", "value": task.get("years"),
             "note": "默认 2019-2026，请按需修改"},
            {"field": "读者 audience", "value": task.get("audience"),
             "note": "请确认面向对象（影响措辞与深度）"},
            {"field": "纳入标准 inclusion", "value": "；".join(task.get("inclusion") or []),
             "note": "默认不额外设置；按用户主题宽松初筛与排序"},
            {"field": "排除标准 exclusion", "value": "；".join(task.get("exclusion") or []),
             "note": "默认不附加影像诊断、研究设计或外部验证限制"},
            {"field": "检索来源 sources", "value": " / ".join(task["sources"]),
             "note": "医学主题默认 PubMed + OpenAlex；每源最多4批，每批50条，后续批次接着获取"},
            {"field": "必答子问题 must_answer", "value": "；".join(task.get("must_answer") or []),
             "note": "自动生成，请按需改写为你的研究问题"},
        ]
        return {"ok": True, "topic": topic, "yaml": yaml_text,
                "confirmations": confirms,
                "missing_topic": not bool((topic or "").strip())}
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": "任务卡草稿生成失败：%s" % exc}


# ------------------------------------------------ 检索计划（中/英检索式）
def plan_step(task_yaml: str) -> dict:
    """复用 2/学术智能体/src/plan_search.py：生成中/英检索式与检索计划。"""
    try:
        _ensure_paths()
        import agent
        mod = _load_module(V2_PLAN, "wb_plan_search")
        task, errors = agent.parse_task(task_yaml)
        if not task:
            return {"ok": False, "error": "；".join(errors or ["任务卡解析失败"])}
        task["years"] = task.get("years_text", task.get("years", ""))
        p = mod.plan(task)
        planned = engine.topic_queries(task)
        p['query_en'] = planned.get('openalex', p['query_en'])
        p['source_queries'] = {s: planned.get(s, p['query_en']) for s in task['sources']}
        p['plan'] = ["1) 研究主题：" + task['topic'],
                     "2) 检索词仅来自主题，不额外加入必答问题或示例限制",
                     "3) 时间范围：" + str(task['years']),
                     "4) 来源：" + ' / '.join(task['sources']),
                     "5) 每源最多连续4批，每批50条；提前取完即停止，后续失败保留前批结果"]
        return {"ok": True, "plan": p, "text": mod.plan_text(p)}
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": "检索计划生成失败：%s" % exc}


# ------------------------------------------------ 运行产物落盘
def _csv(header, rows):
    def cell(v):
        s = "" if v is None else str(v)
        return '"' + s.replace('"', '""') + '"' if ("," in s or '"' in s or "\n" in s) else s
    out = [",".join(header)]
    for r in rows:
        out.append(",".join(cell(r.get(k)) for k in header))
    return "\uFEFF" + "\n".join(out) + "\n"


def save_artifacts(task_yaml, result, internal, plan=None, run_id=None):
    run_id = run_id or run_store.new_id()
    run_dir = run_store.directory(OUTPUTS_DIR, run_id)
    run_dir.mkdir(parents=True, exist_ok=True)
    def w(name, value):
        with open(run_dir / name, 'w', encoding='utf-8') as stream:
            stream.write(value)
    w('task.yaml', task_yaml)
    references_md = '\n\n## 参考文献\n' + '\n'.join(
        '[%s] %s. %s (%s). %s · %s' % (r['n'], r.get('authors', ''), r['title'], r.get('year', ''),
                                     r.get('record_url') or r.get('search_url', ''), r.get('evidence_level', '全文待核'))
        for r in result.get('references', []))
    w('review.md', _review_md(result) + references_md)
    run_store.write_json(run_dir / 'references.json', result.get('references', []))
    run_store.write_json(run_dir / 'screening.json', result.get('screening', {}))
    run_store.write_json(run_dir / 'evidence.json', result.get('evidence_units', []))
    run_store.write_json(run_dir / 'search_records.json', result.get('search', []))
    run_store.write_json(run_dir / 'verification.json', result.get('verification', {}))
    if result.get('synthesis'):
        run_store.write_json(run_dir / 'synthesis.json', result['synthesis'])
    w('retrieval_plan.txt', (plan or {}).get('text', '') + '\n\n实际请求：\n' +
      '\n'.join('%s: %s [%s]' % (r['source'], r['query'], r['status']) for r in result.get('search', [])))
    for source in result.get('search', []):
        if source['source'] in {'mock', 'pubmed', 'openalex', 'semantic_scholar'}:
            run_store.write_json(run_dir / ('raw_' + source['source'] + '.json'), source)
    columns = ('record_id', 'title', 'year', 'venue', 'source', 'doi', 'pmid', 'identifier', 'record_url', 'evidence_level', 'eligibility_status', 'decision', 'reason')
    incl = _csv(columns, result.get('screening', {}).get('included', []))
    excl = _csv(columns, result.get('screening', {}).get('excluded', []))
    uncertain = _csv(columns, result.get('screening', {}).get('uncertain', []))
    candidates = _csv(columns, [p for p in result.get('screening', {}).get('uncertain', []) if p.get('shortlist_candidate')])
    w('included.csv', incl)
    w('excluded.csv', excl)
    w('uncertain.csv', uncertain)
    w('candidates.csv', candidates)
    if internal:
        w('verification_report.md', internal.get('text', '核验未完成'))
    return {'ok': True, 'run_id': run_id, 'run_dir': str(run_dir), 'included_csv': incl,
            'excluded_csv': excl, 'uncertain_csv': uncertain, 'candidates_csv': candidates}


# ================================================================ 第2步
def review_step(task_yaml, corpus_key=None, query='', pico='', decisions=None, parent_run_id=None, progress=None):
    try:
        _ensure_paths()
        import agent
        import sources
        task, errors = agent.parse_task(task_yaml)
        if errors:
            return {'ok': False, 'error': '；'.join(errors)}
        raw = agent._load_yaml(task_yaml)
        task['screening_mode'] = raw.get('screening_mode', 'topic')
        if task['screening_mode'] not in {'topic', 'strict'}:
            return {'ok': False, 'error': 'screening_mode 仅支持 topic 或 strict'}
        explicit = raw.get('queries') or {}
        if not isinstance(explicit, dict) or any(k not in task['sources'] or not isinstance(v, str) for k, v in explicit.items()):
            return {'ok': False, 'error': 'queries 必须是所选来源到检索式文本的映射'}
        if 'mock' in task['sources'] and task['sources'] != ['mock']:
            return {'ok': False, 'error': '演示 mock 必须独立运行，不能与真实检索源混合'}
        if decisions is None:
            decisions = raw.get('screening_decisions') or {}
        validate_decisions(decisions)
        plan = plan_step(task_yaml)
        if not plan.get('ok'):
            return plan
        low = task['topic'].lower()
        if corpus_key == 'lung' or '肺结节' in low or 'nodule' in low:
            corpus = sources.MOCK_PAPERS
        elif corpus_key == 'koa' or '骨关节炎' in low or 'osteoarthritis' in low:
            corpus = demo_data.KOA_MOCK
        else:
            corpus = demo_data.GENERIC_MOCK
        search, papers = engine.retrieve(task, sources, corpus, query,
                                          plan['plan'].get('query_en'), explicit, progress=progress)
        if progress:
            progress({'stage': 'screening', 'message': '获取完成，正在合并题录、筛选并整理引用依据'})
        plan['plan'].setdefault('notes', []).extend(
            '实际请求 %s：%s（%s）' % (r['source'], r['query'], r['status']) for r in search)
        # Failure is not an empty successful review. Keep a queryable failed run.
        result = engine.build_result(task, papers, search, agent, decisions)
        failures = [s for s in search if s['status'] == '请求失败']
        partial = any(s['status'] == '部分获取' for s in search)
        failed = len(failures) == len(search)
        result.update(status='failed' if failed else 'partial' if failures or partial else 'complete',
                      screening_mode=task['screening_mode'],
                      pico=pico, selected_mesh_query=query, decisions=decisions)
        result['notes'].extend('%s：%s' % (s['source'], s['detail']) for s in failures)
        result['notes'].extend(query_scope_notes(query, search))
        plan['plan']['notes'].extend(result['notes'])
        for ref in result['references']:
            ref['tags'] = _evidence_tags(ref['title'] + ' ' + (ref.get('abstract') or ''))
        internal = internal_verify(result)
        run_id = run_store.new_id()
        result['run_id'] = run_id
        result['created_at'] = engine.now()
        result['parent_run_id'] = parent_run_id
        artifacts = save_artifacts(task_yaml, result, internal, plan, run_id)
        response = {'ok': not failed, 'run_id': run_id, 'result': result, 'viz': _review_viz(result),
                    'internal': internal, 'plan': plan, 'artifacts': artifacts,
                    'evidence': evidence_map(result), 'actions': None, 'task_yaml': task_yaml,
                    'query': query, 'pico': pico, 'verification': None}
        if failed:
            response['error'] = '全部检索源请求失败；已保存实际错误，未生成替代文献。'
        run_store.save(OUTPUTS_DIR, run_id, response)
        if progress:
            progress({'stage': 'saved', 'message': '研究记录已保存，推荐%s篇文献' % result['stats']['adopted']})
        return response
    except Exception as exc:
        return {'ok': False, 'error': '检索/综述失败：%s' % exc}


# 题名/摘要启发式证据标签（仅供初筛，需人工核对）
_TAGMAP = [
    ("external validation", "外部验证"), ("multi-center", "多中心"), ("multicenter", "多中心"),
    ("multi-institution", "多中心"), ("prospective", "前瞻性"), ("randomi", "随机对照"),
    ("meta-analysis", "Meta 分析"), ("systematic review", "系统综述"),
    ("deep learning", "深度学习"), ("machine learning", "机器学习"),
    ("radiomics", "影像组学"), ("multi-omics", "多组学"), ("multiomic", "多组学"),
    ("endotyp", "分子分型"), ("cluster", "聚类"), ("nomogram", "列线图"),
]


def _evidence_tags(text: str):
    low = (text or "").lower()
    tags = []
    for kw, lab in _TAGMAP:
        if kw in low and lab not in tags:
            tags.append(lab)
    return tags


# 证据方向启发式归类（仅基于摘要措辞，供快速了解方向；非权威结论）




def evidence_map(result):
    """A topic gets a question map, never an invented proposition or vote."""
    return evidence_policy.overview(result)


# 生活方式行动线索（从摘要里抽取"有证据提及"的做法）








# ------------------------------------------------ 内部引用一致性核验
# 复用 2/学术智能体/src/verify_citations.py（检测虚假引用编号 / 编造 DOI / 链接缺失）
V2_VERIFY = os.path.join(ROOT, "academic-agent-v2", "学术智能体", "src", "verify_citations.py")
V2_PLAN = os.path.join(ROOT, "academic-agent-v2", "学术智能体", "src", "plan_search.py")
V2_PARSE = os.path.join(ROOT, "academic-agent-v2", "学术智能体", "src", "parse_request.py")
OUTPUTS_DIR = str(runtime_paths.outputs_dir())


def _load_module(path: str, name: str):
    import importlib.util
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _review_md(result: dict) -> str:
    """把工作台的 sections 结构拼成 markdown，供内部核验器回查 [n] 引用。"""
    lines = ["# 综述初稿：%s" % result.get("topic", "")]
    for sec in result.get("sections", []):
        lines.append("## " + str(sec.get("title", "")))
        for b in sec.get("blocks", []):
            if b.get("heading"):
                lines.append("### " + str(b["heading"]))
            for p in b.get("paragraphs", []):
                lines.append(p)
        for it in sec.get("items", []):
            lines.append("- " + str(it))
    return "\n".join(lines)


def internal_verify(result: dict) -> dict:
    """调用新版学术智能体的引用核验器，检查综述内引用是否可回查。"""
    try:
        mod = _load_module(V2_VERIFY, "wb_verify_citations")
        refs = [{"n": r.get("n"), "title": r.get("title"),
                 "identifier": r.get("identifier") or ""}
                for r in result.get("references", [])]
        report = mod.verify(_review_md(result), refs)
        return {"ok": True, "report": report,
                "text": mod.report_text(report, "review.md", "references.json")}
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": "内部引用核验不可用：%s" % exc}


def _review_viz(result: dict) -> dict:
    """为可视化准备结构化统计（PRISMA 漏斗、年份/来源分布）。"""
    stats = result.get("stats") or {}
    refs = result.get("references") or []
    years: dict = {}
    for r in refs:
        y = r.get("year")
        if isinstance(y, int):
            years[y] = years.get(y, 0) + 1
    year_list = [{"year": y, "count": years[y]} for y in sorted(years)]
    srcs: dict = {}
    for r in refs:
        s = r.get("source") or "—"
        srcs[s] = srcs.get(s, 0) + 1
    return {
        "funnel": [
            {"label": "本批获取", "value": stats.get("retrieved", stats.get("hit", 0))},
            {"label": "去重后", "value": stats.get("dedup", 0)},
            {"label": "通过筛选", "value": stats.get("included", 0)},
            {"label": "进入材料稿", "value": stats.get("adopted", 0)},
            {"label": "存疑待核", "value": stats.get("uncertain", 0)},
            {"label": "排除", "value": stats.get("excluded", 0)},
        ],
        "years": year_list,
        "sources": [{"name": k, "count": v} for k, v in sorted(srcs.items())],
    }


# ================================================================ 第3步
def _norm(text: str) -> str:
    return re.sub(r"[\W_]+", "", (text or "").casefold())


def _get_json(url: str, timeout: int = HTTP_TIMEOUT) -> dict:
    req = urllib.request.Request(url, headers={"User-Agent": UA,
                                               "Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8", "replace"))


# ------------------------------------------------ PubMed 命中数（在线）
def _esearch_count(term: str) -> int:
    params = urllib.parse.urlencode({
        "db": "pubmed", "retmode": "json", "retmax": 0, "term": term})
    url = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esearch.fcgi?" + params
    data = _get_json(url, timeout=12)
    result = data.get('esearchresult', {})
    if data.get('error') or result.get('ERROR') or result.get('errorlist'):
        raise ValueError('PubMed 返回检索错误，不能作为零命中')
    return int(result['count'])


def count_step(query: str, task_yaml: str = '') -> dict:
    """用 NCBI E-utilities esearch 查命中数；离线则返回友好失败。"""
    query = (query or "").strip()
    if not query:
        return {"ok": False, "error": "检索式为空"}
    scope = '未限定时间窗'
    try:
        if task_yaml.strip():
            _ensure_paths()
            import agent
            task, errors = agent.parse_task(task_yaml)
            if errors:
                return {'ok': False, 'error': '请先确认任务卡时间窗：' + '；'.join(errors)}
            lo, hi = task['years']
            query = '(%s) AND (%d/01/01[Date - Publication] : %d/12/31[Date - Publication])' % (query, lo, hi)
            scope = task['years_text']
        count = _esearch_count(query)
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": "无法查询 PubMed 命中数（可能无外网）：%s" % exc}
    hint = '时间窗：%s；这是 PubMed 报告总匹配数，未获取或筛选全部记录，不能据此判断检索质量。' % scope
    return {"ok": True, "count": count, "hint": hint, 'query': query, 'years': scope}


def tune_step(items: list) -> dict:
    """自动调优：贪心移除概念，直到命中数 > 0（需联网）。

    items: [{"i": 序号, "text": 显示词, "clause": 该概念子句}]
    返回命中 > 0 的最小保留组合（keep = 序号列表）。
    """
    items = [it for it in items if (it.get("clause") or "").strip()]
    if len(items) < 1:
        return {"ok": False, "error": "没有可调优的概念"}

    def cnt(sub):
        return _esearch_count("\n  AND ".join(x["clause"] for x in sub))

    # 每个概念单独命中数（越少 = 越窄，优先移除）
    single = {}
    for it in items:
        try:
            single[it["i"]] = cnt([it])
        except Exception:  # noqa: BLE001
            single[it["i"]] = None
        time.sleep(0.34)

    def narrowness(it):
        c = single.get(it["i"])
        return 10 ** 9 if c is None else c

    cur = list(items)
    removed = []
    try:
        total = cnt(cur)
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": "无法访问 PubMed：%s" % exc}
    time.sleep(0.34)
    trail = [{"kept": [x["text"] for x in cur], "count": total}]

    # 0 命中时，反复移除"最窄"的概念，直到有命中（保留宽泛/核心概念）
    while total == 0 and len(cur) > 1:
        narrowest = min(cur, key=narrowness)
        removed.append(narrowest["text"])
        cur = [x for x in cur if x is not narrowest]
        try:
            total = cnt(cur)
        except Exception:  # noqa: BLE001
            break
        time.sleep(0.34)
        trail.append({"kept": [x["text"] for x in cur], "count": total})

    singles = [{"text": it["text"], "count": single.get(it["i"])} for it in items]
    return {"ok": True, "keep": [x["i"] for x in cur], "count": total,
            "removed": removed, "singles": singles, "trail": trail}


def _pubmed_retraction(doi: str) -> bool:
    """查 PubMed 该 DOI 是否被标记为撤稿（失败一律返回 False，不误报）。"""
    if not doi:
        return False
    try:
        p1 = urllib.parse.urlencode({"db": "pubmed", "retmode": "json",
                                     "retmax": 1, "term": "%s[AID]" % doi})
        r1 = _get_json("https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esearch.fcgi?" + p1, 12)
        ids = (r1.get("esearchresult") or {}).get("idlist") or []
        if not ids:
            return False
        time.sleep(0.34)
        p2 = urllib.parse.urlencode({"db": "pubmed", "id": ids[0], "retmode": "json"})
        r2 = _get_json("https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esummary.fcgi?" + p2, 12)
        rec = (r2.get("result") or {}).get(ids[0]) or {}
        return any("retract" in str(t).lower() for t in (rec.get("pubtype") or []))
    except Exception:  # noqa: BLE001
        return False


def _verify_one(ref):
    doi = re.sub(r'^https?://(?:dx\.)?doi\.org/', '', str(ref.get('doi') or ref.get('identifier') or '').strip(), flags=re.I)
    base = {'matched_by': '—', 'published': '—', 'retracted': False}
    if ref.get('source') == 'mock':
        return dict(base, verdict='演示未核验', reason='mock 语料不能用于真实题录核验')
    if not re.fullmatch(r'10\.\d{4,9}/\S+', doi):
        return dict(base, verdict='暂无法核验', reason='缺少可解析 DOI；保留来源链接供人工核查')
    try:
        data = _get_json('https://api.crossref.org/works/' + urllib.parse.quote(doi, safe=''))
        msg = data.get('message') or {}
        title = (msg.get('title') or [''])[0]
        years = set()
        for key in ('published-print', 'published-online', 'published', 'issued'):
            parts = (msg.get(key) or {}).get('date-parts') or []
            if parts and parts[0]:
                years.add(int(parts[0][0]))
        author = (msg.get('author') or [{}])[0]
        updates = (msg.get('update-to') or []) + (msg.get('updated-by') or [])
        retract = any('retract' in str(u.get('type', '')).lower() for u in updates)
        origin = 'Crossref'
    except urllib.error.HTTPError as exc:
        if exc.code != 404:
            return dict(base, verdict='暂无法核验', reason='Crossref HTTP %s' % exc.code)
        try:
            term = urllib.parse.urlencode({'db': 'pubmed', 'retmode': 'json', 'term': doi + '[AID]'})
            data = _get_json('https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esearch.fcgi?' + term)
            found = data.get('esearchresult')
            if not isinstance(found, dict) or found.get('errorlist') or found.get('ERROR'):
                raise ValueError('PubMed 检索返回错误')
            ids = found.get('idlist', [])
            if not ids:
                return dict(base, verdict='未检出', reason='Crossref 与 PubMed 当前未检出；不能据此确认论文不存在')
            time.sleep(0.4)
            q = urllib.parse.urlencode({'db': 'pubmed', 'retmode': 'json', 'id': ','.join(ids)})
            records = _get_json('https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esummary.fcgi?' + q).get('result', {})
            msg = next((records[i] for i in ids if any(
                a.get('idtype') == 'doi' and str(a.get('value', '')).lower() == doi.lower()
                for a in records.get(i, {}).get('articleids', []))), None)
            if not msg:
                return dict(base, verdict='暂无法核验', reason='PubMed 候选记录未能确认 DOI')
            title = msg.get('title', '')
            years = {int(y) for y in re.findall(r'\b(?:19|20)\d{2}\b', str(msg.get('pubdate', '')) + ' ' + str(msg.get('epubdate', '')))}
            author = {'name': (msg.get('authors') or [{}])[0].get('name', '')}
            retract = any('retracted publication' in str(t).lower() for t in msg.get('pubtype', []))
            origin = 'PubMed'
        except Exception as exc:
            return dict(base, verdict='暂无法核验', reason='Crossref 未检出，PubMed 核查失败：%s' % exc)
    except Exception as exc:
        return dict(base, verdict='暂无法核验', reason='外部查询未完成：%s' % exc)
    if not title:
        return dict(base, verdict='暂无法核验', reason='数据库记录缺少题名')
    # Compare supplied fields only; DOI-only inputs resolve a record, not a full citation.
    conflicts = []
    supplied_title = ref.get('title') or ''
    if supplied_title and _norm(supplied_title) != _norm(title):
        conflicts.append('题名')
    if ref.get('year') and years and int(ref['year']) not in years:
        conflicts.append('年份')
    supplied_author = ref.get('author') or ''
    family = author.get('family') or (author.get('name') or '').split(' ')[0]
    if supplied_author and (not family or _norm(family) not in _norm(supplied_author)):
        conflicts.append('第一作者')
    if retract:
        verdict, reason = '已撤稿', '数据库记录包含撤稿标记，须人工处理'
    elif conflicts:
        verdict = '年份待核' if conflicts == ['年份'] else '指向他文'
        reason = '题录冲突：' + '、'.join(conflicts) + '；请人工核查' + ('，可能涉及在线发表与印刷卷期年份差异' if conflicts == ['年份'] else '')
    elif not supplied_title:
        verdict, reason = '记录可解析', '已定位 DOI 记录；未提供完整题录供交叉比对'
    else:
        verdict, reason = '通过', '已提供题录与来源记录匹配；正文主张仍待人工核查'
    return dict(base, verdict=verdict, reason=reason, matched_by=origin,
                published=title, retracted=retract, checked_at=engine.now())


def verify_step(refs=None, online=True, run_id=None):
    with RUN_LOCK:
        snapshot = run_store.load(OUTPUTS_DIR, run_id) if run_id else None
        refs = snapshot['result']['references'] if snapshot else (refs or [])
        out = []
        for ref in refs:
            rec = _verify_one(ref) if online else {'verdict': '暂无法核验', 'reason': '未启用联网核验',
                                                  'matched_by': '未查询', 'published': '—', 'retracted': False}
            if ref.get('source') == 'mock':
                rec.update(verdict='演示未核验', reason='mock 演示，不能确认题录真实性')
            rec.update({k: ref.get(k) for k in ('record_id', 'n', 'title', 'author', 'authors', 'year', 'doi', 'record_url', 'search_url')})
            out.append(rec)
        summary = {}
        for r in out:
            summary[r['verdict']] = summary.get(r['verdict'], 0) + 1
        response = {'ok': True, 'run_id': run_id, 'refs': out, 'offline': not online,
                    'summary': [{'name': k, 'count': v} for k, v in summary.items()],
                    'checked_at': engine.now(), 'claim_support': '待人工核查'}
        if snapshot:
            snapshot['verification'] = response
            snapshot['result']['verification'] = response
            for ref in snapshot['result']['references']:
                v = next(r for r in out if r['record_id'] == ref['record_id'])
                ref['verification_status'] = v['verdict']
            snapshot['artifacts'] = save_artifacts(snapshot['task_yaml'], snapshot['result'],
                                                   snapshot['internal'], snapshot['plan'], run_id)
            run_store.save(OUTPUTS_DIR, run_id, snapshot)
        return response


# ================================================================ 第4步
def sq3r_step(run_id=None) -> dict:
    """复用 sq3r-academic-reading 技能：返回技能元信息与五步流程。"""
    try:
        with open(SKILL_MD, "r", encoding="utf-8") as fh:
            text = fh.read()
    except OSError as exc:
        return {"ok": False, "error": "未找到 SQ3R 技能：%s" % exc}

    name = ""
    desc = ""
    m = re.search(r"^name:\s*(.+)$", text, re.M)
    if m:
        name = m.group(1).strip()
    m = re.search(r"^description:\s*(.+)$", text, re.M)
    if m:
        desc = m.group(1).strip()
    steps = re.findall(r"^### Step \d+ — (.+)$", text, re.M)
    gates = len(re.findall(r"^- \[ \]", text, re.M))
    modes = re.findall(r"`(standard|strict)`", text)
    result = {
        "ok": True,
        "name": name,
        "description": desc,
        "steps": steps,
        "quality_gates": gates,
        "modes": sorted(set(modes)),
    }
    if run_id:
        snapshot = load_run(run_id)
        result['reading_items'] = [
            {'n': ref['n'], 'title': ref['title'], 'record_url': ref.get('record_url'),
             'questions': snapshot['result'].get('review_questions', []),
             'abstract_quotes': ref.get('question_assessment', {}).get('quotes', []),
             'status': '摘要级阅读准备；阅读全文并核对方法、图表及结果后再作结论'}
            for ref in snapshot['result'].get('references', [])[:3]]
    return result


# ================================================================ 健康检查
def health() -> dict:
    """报告四个组件是否就位，供页面显示与排障。"""
    mesh_db = os.path.join(MESH_DIR, "data", "mesh.db")
    items = [
        {"name": "① mesh-query 检索式", "path": MESH_DIR,
         "ok": os.path.exists(mesh_db),
         "hint": "" if os.path.exists(mesh_db) else "缺少 mesh-query/data/mesh.db"},
        {"name": "② 学术智能体", "path": AGENT_DIR,
         "ok": os.path.exists(os.path.join(AGENT_DIR, "agent.py")),
         "hint": "" if os.path.exists(os.path.join(AGENT_DIR, "agent.py")) else "缺少 学术智能体/agent.py"},
        {"name": "③ 引用核验", "path": "Crossref/PubMed", "ok": True,
         "hint": "联网查题录；断网或查询失败保持待核，不替换成缓存示例"},
        {"name": "④ SQ3R 精读技能", "path": SKILL_MD,
         "ok": os.path.exists(SKILL_MD),
         "hint": "" if os.path.exists(SKILL_MD) else "缺少 sq3r-skill/skill/SKILL.md"},
    ]
    return {"ok": all(i["ok"] for i in items), "items": items,
            "app_id": runtime_paths.APP_ID, "version": runtime_paths.VERSION,
            "python": sys.version.split()[0], "root": ROOT}


# ================================================================ 运行记录









# ================================================================ 一键跑通
def run_all(pico='', online=False, task_yaml='', query=''):
    if not task_yaml.strip():
        return {'ok': False, 'error': '必须提供已确认任务卡，不能自动换成固定示例'}
    mesh = mesh_step(pico)
    if not mesh.get('ok'):
        return mesh
    review = review_step(task_yaml, query=query or mesh.get('query', ''), pico=pico)
    if not review.get('ok'):
        return review
    verification = verify_step(online=online, run_id=review['run_id'])
    return {'ok': True, 'run_id': review['run_id'], 'step1_mesh': mesh,
            'step2_review': review, 'step3_verify': verification, 'step4_sq3r': sq3r_step()}


if __name__ == "__main__":
    import sys as _sys
    _sys.stdout.reconfigure(encoding="utf-8")
    print(json.dumps(run_all(online=False), ensure_ascii=False, indent=2))


def validate_decisions(decisions):
    if not isinstance(decisions, dict):
        raise ValueError('screening_decisions 必须是 record_id 到人工判定的映射')
    for record_id, value in decisions.items():
        if not re.fullmatch(r'[a-f0-9]{24}', str(record_id)) or not isinstance(value, dict):
            raise ValueError('无效文献编号或判定格式')
        if value.get('decision') not in {'included', 'uncertain', 'excluded'} or not str(value.get('reason', '')).strip():
            raise ValueError('人工判定须提供 decision（included/uncertain/excluded）和 reason')


def review_decisions(run_id, decisions):
    validate_decisions(decisions)
    with RUN_LOCK:
        snapshot = run_store.load(OUTPUTS_DIR, run_id)
        result = snapshot['result']
        known = {p['record_id'] for group in ('included', 'uncertain', 'excluded') for p in result['screening'][group]}
        if any(key not in known for key in decisions):
            return {'ok': False, 'error': '判定包含不属于当前运行的文献'}
        _ensure_paths()
        import agent
        task, errors = agent.parse_task(snapshot['task_yaml'])
        if errors:
            return {'ok': False, 'error': '；'.join(errors)}
        task['screening_mode'] = result.get('screening_mode', 'strict')
        combined = dict(result.get('decisions', {}))
        combined.update({k: dict(v, checked_at=engine.now()) for k, v in decisions.items()})
        papers = [p for record in result['search'] for p in record['records']]
        rebuilt = engine.build_result(task, papers, result['search'], agent, combined)
        rebuilt['notes'] = list(dict.fromkeys(rebuilt['notes'] + result.get('notes', []) +
                                             query_scope_notes(snapshot.get('query', ''), result['search'])))
        child_id = run_store.new_id()
        rebuilt.update(run_id=child_id, created_at=engine.now(), parent_run_id=run_id,
                       decisions=combined, pico=snapshot.get('pico', ''),
                       selected_mesh_query=snapshot.get('query', ''), status=result['status'])
        snapshot.update(run_id=child_id, result=rebuilt, verification=None,
                        internal=internal_verify(rebuilt), viz=_review_viz(rebuilt), evidence=evidence_map(rebuilt))
        snapshot['artifacts'] = save_artifacts(snapshot['task_yaml'], rebuilt, snapshot['internal'], snapshot['plan'], child_id)
        run_store.save(OUTPUTS_DIR, child_id, snapshot)
        return snapshot


def load_run(run_id):
    snapshot = run_store.load(OUTPUTS_DIR, run_id)
    result = snapshot['result']
    # Correct the presentation of legacy snapshots without rewriting evidence
    # or inventing metadata the original request did not save.
    engine.refresh_summary(result)
    _ensure_paths()
    import agent
    task, errors = agent.parse_task(snapshot['task_yaml'])
    if not errors:
        task['screening_mode'] = result.get('screening_mode', 'strict')
        result['screening_capability'] = engine.screening_capability(task, agent)
    notes = list(result.get('notes', [])) + query_scope_notes(snapshot.get('query', ''), result.get('search', []))
    if result.get('screening_capability', {}).get('uninterpreted_rules'):
        notes.append('自动筛选受限：存在当前规则不能解释的条件，存疑不等于不相关。')
    result['notes'] = list(dict.fromkeys(notes))
    snapshot['viz'] = _review_viz(result)
    snapshot['evidence'] = evidence_map(result)
    return snapshot


def query_scope_notes(query, search):
    if not query:
        return []
    used = [s['source'] for s in search if s.get('query_origin') == 'selected_mesh' or
            (s['source'] == 'pubmed' and s.get('query') == query)]
    if used:
        return ['MeSH 概念选择仅用于 %s；其余来源按各自实际检索式执行，数据库总匹配不代表符合纳入标准。' % '、'.join(used)]
    return ['注意：上方 MeSH 式未用于本次检索。当前来源使用下列各自的实际检索式，改变 MeSH 勾选不会改变它们的查询。']


def list_runs():
    records = []
    if os.path.isdir(OUTPUTS_DIR):
        for name in os.listdir(OUTPUTS_DIR):
            if not re.fullmatch(r'[a-f0-9]{32}', name):
                continue
            try:
                item = load_run(name)
                result = item['result']
                records.append({'run_id': name, 'topic': result['topic'], 'created_at': result['created_at'],
                                'status': result['status'], 'parent_run_id': result.get('parent_run_id'),
                                'stats': result['stats']})
            except (OSError, ValueError, KeyError):
                continue
    return {'ok': True, 'runs': sorted(records, key=lambda r: r['created_at'], reverse=True)}


def archive_run(run_id):
    """Hide a run from history, preserving artifacts for recovery."""
    with RUN_LOCK:
        source = run_store.directory(OUTPUTS_DIR, run_id)
        destination = source.with_name('.archived-' + run_id)
        root = os.path.realpath(OUTPUTS_DIR)
        if os.path.dirname(os.path.realpath(source)) != root or os.path.dirname(os.path.realpath(destination)) != root:
            raise ValueError('运行目录超出范围')
        os.replace(source, destination)
        return {'ok': True, 'run_id': run_id, 'archived': True}


def synthesis_step(run_id, provider='extractive', progress=None):
    """Create a new draft version; never replace a source or its verification."""
    import evidence_synthesis
    snapshot = load_run(run_id)
    if provider not in {'extractive', 'claude-cli'}:
        raise ValueError('不支持的整理方式')
    if not snapshot.get('ok') or not snapshot['result'].get('references'):
        raise ValueError('请先完成检索并选定文献')
    if progress:
        progress({'stage': 'synthesis', 'message': '正在根据已选文献整理中文初稿与逐段摘要依据' if provider == 'claude-cli' else '正在整理原始摘要证据'})
    try:
        document = evidence_synthesis.synthesize(snapshot['result'], provider=provider)
    except (RuntimeError, ValueError) as exc:
        return {'ok': False, 'stage': 'synthesis', 'error': str(exc)}
    with RUN_LOCK:
        result = snapshot['result']
        child_id = run_store.new_id()
        result.setdefault('source_material_sections', result.get('sections', []))
        result['synthesis'] = document
        if snapshot.get('replay'):
            snapshot['replay']['note'] = '基于已保存的真实检索材料，本次重新整理了初稿；未重新检索数据库。'
            result['replay'] = snapshot['replay']
        result['sections'] = [
            {'index': i + 1, 'title': section['heading'], 'blocks': [
                {'heading': '', 'paragraphs': [p['text'] + ' ' + ''.join('[%s]' % a['n'] for a in p['evidence'])]}
                for p in section['paragraphs']], 'items': []}
            for i, section in enumerate(document['sections'])]
        result['sections'].append({'index': len(result['sections']) + 1, 'title': '材料范围与待核事项',
                                   'blocks': [], 'items': document.get('limitations', [])})
        result.update(run_id=child_id, created_at=engine.now(), parent_run_id=run_id, verification=None)
        result['word_count'] = sum(len(p) for s in result['sections'] for b in s['blocks'] for p in b['paragraphs'])
        snapshot.update(run_id=child_id, result=result, verification=None,
                        internal=internal_verify(result), viz=_review_viz(result), evidence=evidence_map(result))
        snapshot['artifacts'] = save_artifacts(snapshot['task_yaml'], result, snapshot['internal'], snapshot['plan'], child_id)
        run_store.write_json(run_store.directory(OUTPUTS_DIR, child_id) / 'synthesis.json', document)
        run_store.save(OUTPUTS_DIR, child_id, snapshot)
    return snapshot


def task_card(task_yaml, updates=None):
    """Form editing preserves advanced source queries and custom rules."""
    _ensure_paths()
    import agent
    raw = agent._load_yaml(task_yaml)
    if not isinstance(raw, dict):
        raise ValueError('请先载入示例或生成任务卡')
    allowed = {'topic', 'years', 'sources', 'max_papers', 'must_answer', 'inclusion', 'exclusion', 'screening_mode'}
    if updates is not None:
        if not isinstance(updates, dict) or set(updates) - allowed:
            raise ValueError('任务卡更新包含不支持的字段')
        if 'max_papers' in updates and (type(updates['max_papers']) is not int or not 5 <= updates['max_papers'] <= 20):
            raise ValueError('推荐上限请设为5至20篇')
        if 'sources' in updates and (not isinstance(updates['sources'], list) or not updates['sources'] or any(s not in {'pubmed', 'openalex', 'semantic_scholar'} for s in updates['sources'])):
            raise ValueError('请至少选择一个真实检索来源')
        for name in ('must_answer', 'inclusion', 'exclusion'):
            if name in updates and (not isinstance(updates[name], list) or any(not isinstance(x, str) for x in updates[name])):
                raise ValueError('研究问题和筛选条件需要是文本列表')
        raw.update(updates)
    def serialize(mapping, level=0):
        lines = []
        for name, value in mapping.items():
            prefix = ' ' * level + str(name) + ':'
            if isinstance(value, dict) and value:
                lines.extend([prefix, serialize(value, level + 2)])
            elif isinstance(value, list) and value:
                lines.append(prefix)
                lines.extend(' ' * (level + 2) + '- ' + json.dumps(v, ensure_ascii=False) for v in value)
            else:
                lines.append(prefix + ' ' + json.dumps(value, ensure_ascii=False))
        return '\n'.join(lines)
    updated = serialize(raw) + '\n'
    _, errors = agent.parse_task(updated)
    if errors:
        raise ValueError('；'.join(errors))
    return {'ok': True, 'task': {k: raw.get(k) for k in allowed}, 'task_yaml': updated}


def export_bundle(run_id):
    import io
    import zipfile
    directory = run_store.directory(OUTPUTS_DIR, run_id)
    if not (directory / 'snapshot.json').exists():
        raise FileNotFoundError('运行记录不存在')
    names = ['task.yaml', 'review.md', 'references.json', 'screening.json', 'evidence.json',
             'search_records.json', 'verification.json', 'retrieval_plan.txt', 'included.csv',
             'excluded.csv', 'uncertain.csv', 'candidates.csv', 'verification_report.md',
             'synthesis.json', 'snapshot.json', 'raw_pubmed.json', 'raw_openalex.json', 'raw_semantic_scholar.json']
    data = io.BytesIO()
    with zipfile.ZipFile(data, 'w', compression=zipfile.ZIP_DEFLATED) as archive:
        for name in names:
            target = directory / name
            if target.is_file() and not target.is_symlink():
                archive.write(target, arcname=name)
    return data.getvalue()


def showcase_cases():
    from pathlib import Path
    manifest = Path(HERE) / 'samples' / 'manifest.json'
    if not manifest.exists():
        return {'ok': True, 'cases': []}
    return {'ok': True, 'cases': json.loads(manifest.read_text(encoding='utf-8'))}


def replay_case(case_id):
    """Only curated local research snapshots; replay is always explicit."""
    import gzip
    from pathlib import Path
    case = next((c for c in showcase_cases()['cases'] if c['id'] == case_id), None)
    if not case:
        raise ValueError('未找到已收录的真实案例')
    with gzip.open(Path(HERE) / 'samples' / (case['id'] + '.json.gz'), 'rt', encoding='utf-8') as stream:
        snapshot = json.load(stream)
    with RUN_LOCK:
        result = snapshot['result']
        original_id = snapshot['run_id']
        child_id = run_store.new_id()
        provenance = {'mode': 'recorded_replay', 'case_id': case['id'], 'title': case['title'],
                      'original_run_id': original_id, 'recorded_at': result.get('created_at'),
                      'note': '已保存的真实检索案例回放；未在此次重新查询数据库或模型'}
        snapshot.update(run_id=child_id, replay=provenance)
        result.update(run_id=child_id, created_at=engine.now(), parent_run_id=original_id, replay=provenance)
        if snapshot.get('verification'):
            snapshot['verification'].update(run_id=child_id, replay=True)
            result['verification'] = snapshot['verification']
        snapshot['artifacts'] = save_artifacts(snapshot['task_yaml'], result, snapshot['internal'], snapshot['plan'], child_id)
        run_store.save(OUTPUTS_DIR, child_id, snapshot)
    return snapshot
