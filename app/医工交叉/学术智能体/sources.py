# -*- coding: utf-8 -*-
"""检索源适配器：openalex / semantic_scholar / pubmed / mock。

约束（见任务卡要求）：
  - 单次请求 10 秒超时、最多重试 2 次（合计最多 3 次尝试）；
  - 任一来源失败只抛 SourceError（友好中文说明）；
  - 单页函数保持旧签名；``search_paginated`` 顺序翻页（最多 4 页 × 50 条），
    后续页失败时保留已取得的记录并标记 partial。
"""
import json
import re
import socket
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET

TIMEOUT = 10       # 单次请求超时（秒）
MAX_RETRY = 2      # 最多重试 2 次
UA = "academic-agent/0.1 (local MVP; +python-urllib)"


class SourceError(Exception):
    """检索失败（已转成友好中文说明）。"""


class SearchResults(list):
    """List-compatible records with the response envelope retained for auditing.

    A page length is never a substitute for the database's reported total.

    Paginated calls additionally expose ``pages`` (one audit record per
    attempted batch), ``errors`` (page failures / pagination anomalies),
    ``status`` (complete/partial/failed), ``exhausted`` and ``request_urls``.
    They remain ordinary lists, so existing clients keep working.
    """
    def __init__(self, papers=(), *, total=None, limit=None, request_url="",
                 response_meta=None, pages=None, errors=None, status=None,
                 exhausted=None, request_urls=None):
        super().__init__(papers)
        self.total = int(total) if str(total).isdigit() else None
        self.limit = limit
        self.request_url = request_url
        self.response_meta = response_meta or {}
        self.pages = list(pages or [])
        self.errors = list(errors or [])
        self.status = status
        self.exhausted = exhausted
        if request_urls is not None:
            self.request_urls = list(request_urls)
        else:
            self.request_urls = [request_url] if request_url else []
        self.request_limit = limit


def friendly_error(exc):
    """把任意异常转成一句可展示的中文说明（不抛栈）。"""
    if isinstance(exc, SourceError):
        return str(exc)
    if isinstance(exc, urllib.error.HTTPError):
        code = getattr(exc, "code", 0)
        if code == 429:
            return "HTTP 429 接口限流"
        if code in (401, 403):
            return "HTTP %d 接口拒绝访问" % code
        if code >= 500:
            return "HTTP %d 服务端错误" % code
        return "HTTP %s" % code
    if isinstance(exc, urllib.error.URLError):
        reason = getattr(exc, "reason", None)
        if isinstance(reason, (socket.timeout, TimeoutError)) or "timed out" in str(reason):
            return "请求超时（%d 秒）" % TIMEOUT
        return "网络不可达（%s）" % (reason or exc)
    if isinstance(exc, (socket.timeout, TimeoutError)):
        return "请求超时（%d 秒）" % TIMEOUT
    if isinstance(exc, json.JSONDecodeError):
        return "返回内容不是合法 JSON"
    if isinstance(exc, ET.ParseError):
        return "返回内容不是合法 XML"
    if isinstance(exc, OSError):
        return "网络异常（%s）" % exc
    return "%s" % exc


def _get_json(url):
    """GET 并解析 JSON：10 秒超时，最多重试 2 次。"""
    last = None
    for attempt in range(MAX_RETRY + 1):
        try:
            req = urllib.request.Request(
                url, headers={"User-Agent": UA, "Accept": "application/json"})
            with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
                raw = resp.read()
            return json.loads(raw.decode("utf-8", "replace"))
        except Exception as exc:            # noqa: BLE001 全部收敛成友好提示
            last = exc
            if attempt < MAX_RETRY:
                time.sleep(0.6 * (attempt + 1))
    raise SourceError(friendly_error(last))


def _get_bytes(url):
    """GET raw bytes (PubMed EFetch XML) with the same bounded retry policy.

    Only the *same* request is retried; callers never substitute page 1 for a
    later page.
    """
    last = None
    for attempt in range(MAX_RETRY + 1):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": UA})
            with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
                return resp.read()
        except Exception as exc:            # noqa: BLE001
            last = exc
            if attempt < MAX_RETRY:
                time.sleep(0.6 * (attempt + 1))
    raise SourceError(friendly_error(last))


def _as_int(value):
    """Best-effort non-negative int (APIs return counts as strings)."""
    try:
        ivalue = int(value)
    except (TypeError, ValueError):
        return None
    return ivalue if ivalue >= 0 else None


def _paper(title, authors, year, abstract, venue, source, url="", doi=""):
    authors = [a for a in (authors or []) if a]
    return {
        "title": (title or "").strip(),
        "authors": authors,
        "year": year if isinstance(year, int) else None,
        "abstract": (abstract or "").strip(),
        "venue": (venue or "").strip(),
        "source": source,
        "url": url or "",
        "doi": doi or "",
    }


def _abstract_from_inverted(index):
    """OpenAlex 的 abstract_inverted_index 还原成文本。"""
    if not isinstance(index, dict):
        return ""
    buf = []
    for word, positions in index.items():
        for pos in positions or []:
            buf.append((pos, word))
    buf.sort()
    return " ".join(w for _, w in buf)


def _openalex_url(topic, years, per_page, cursor=None):
    lo, hi = years
    params = [
        ("search", topic),
        ("filter", "from_publication_date:%d-01-01,to_publication_date:%d-12-31" % (lo, hi)),
        ("per-page", per_page),
    ]
    if cursor is not None:
        # A stable sort is required for cursor paging; cursor=* starts page 1.
        params.append(("sort", "relevance_score:desc"))
        params.append(("cursor", cursor))
    return "https://api.openalex.org/works?" + urllib.parse.urlencode(params)


def _parse_openalex(data):
    if not isinstance(data, dict) or not isinstance(data.get("results"), list):
        raise SourceError("OpenAlex 返回错误或缺少 results，不能当作零命中")
    out = []
    for w in (data.get("results") or []):
        if not isinstance(w, dict):
            continue
        authors = []
        for a in (w.get("authorships") or []):
            name = ((a or {}).get("author") or {}).get("display_name")
            if name:
                authors.append(name)
        loc = w.get("primary_location") or {}
        venue = ((loc.get("source") or {}).get("display_name")) or ""
        out.append(_paper(
            w.get("title") or w.get("display_name") or "",
            authors,
            w.get("publication_year"),
            _abstract_from_inverted(w.get("abstract_inverted_index")),
            venue or "OpenAlex",
            "openalex",
            w.get("id") or "",
            (w.get("doi") or "").replace("https://doi.org/", ""),
        ))
        out[-1]["source_record"] = w
    return out, (data.get("meta") or {})


def search_openalex(topic, years, limit, *, cursor=None):
    """Single-page OpenAlex search (unchanged public behaviour).

    ``cursor`` is optional so the paginator can request a specific page without
    changing the existing three-argument call.
    """
    page_size = max(1, min(50, limit))
    url = _openalex_url(topic, years, page_size, cursor=cursor)
    data = _get_json(url)
    out, meta = _parse_openalex(data)
    return SearchResults(out, total=meta.get("count"), limit=page_size,
                         request_url=url, response_meta=meta)


def _semantic_url(topic, years, per_page, offset=None):
    params = [
        ("query", topic),
        ("year", "%d-%d" % years),
        ("limit", per_page),
        ("fields", "title,authors,year,abstract,venue,externalIds,url"),
    ]
    if offset is not None:
        params.append(("offset", offset))
    return "https://api.semanticscholar.org/graph/v1/paper/search?" + urllib.parse.urlencode(params)


def _parse_semantic(data):
    if not isinstance(data, dict) or not isinstance(data.get("data"), list):
        raise SourceError("Semantic Scholar 返回错误或缺少 data，不能当作零命中")
    out = []
    for p in (data.get("data") or []):
        if not isinstance(p, dict):
            continue
        authors = [a.get("name") for a in (p.get("authors") or []) if (a or {}).get("name")]
        ext = p.get("externalIds") or {}
        out.append(_paper(
            p.get("title") or "",
            authors,
            p.get("year"),
            p.get("abstract") or "",
            p.get("venue") or "Semantic Scholar",
            "semantic_scholar",
            p.get("url") or "",
            (ext.get("DOI") or ""),
        ))
        out[-1]["source_record"] = p
    return out


def search_semantic_scholar(topic, years, limit, *, offset=None):
    """Single-page Semantic Scholar search (unchanged public behaviour)."""
    page_size = max(1, min(100, limit))
    url = _semantic_url(topic, years, page_size, offset=offset)
    data = _get_json(url)
    out = _parse_semantic(data)
    return SearchResults(out, total=data.get("total"), limit=page_size,
                         request_url=url,
                         response_meta={k: data[k] for k in ("total", "offset", "next") if k in data})


def _mock(title, authors, year, venue, abstract, keyword_note=""):
    return _paper(title, authors, year, abstract, venue, "mock",
                  "mock://paper/%d" % year, "")


# 写死的样例文献：前 3 条符合纳入标准，后 5 条覆盖各种"不符合"情形，
# 便于无网环境验证筛选逻辑（命中排除标准 / 未满足纳入标准 / 年份越界）。
MOCK_PAPERS = [
    _mock(
        "Cross-vendor generalization of low-dose CT lung nodule detection via "
        "style-consistent domain randomization",
        ["Wang Lei", "Zhang Min"], 2023, "Medical Image Analysis",
        "Low-dose CT lung nodule detection degrades when the scanner vendor changes. "
        "We propose style-consistent domain randomization and multi-center external "
        "validation across four hospitals, showing that domain shift between scanners "
        "can be decomposed into physical acquisition shift and patient case-mix shift. "
        "Cross-scanner evaluation and stratified analysis are reported."),
    _mock(
        "Federated domain adaptation for multi-center pulmonary nodule detection "
        "under scanner shift",
        ["Chen Yang", "Liu Hua", "Patel R."], 2022, "IEEE TMI",
        "We present a federated domain adaptation framework for pulmonary nodule "
        "detection that addresses domain shift across institutions. External validation "
        "on three centers and cross-vendor CT scanners demonstrates improved "
        "generalization compared with single-center training. Stratified results by "
        "scanner manufacturer and nodule size are provided."),
    _mock(
        "External validation of a deep learning nodule detector across CT manufacturers: "
        "a retrospective study",
        ["Kim Soo-jin", "Novak P."], 2021, "European Radiology",
        "This retrospective study externally validates a deep learning lung nodule "
        "detector on low-dose CT from three independent centers and multiple CT "
        "manufacturers. Performance drops under scanner domain shift, and disease "
        "spectrum differences between screening cohorts further affect sensitivity. "
        "We recommend stratified external validation before clinical deployment."),
    _mock(
        "Improving backbone networks for lung nodule classification on LIDC-IDRI "
        "leaderboard ranking",
        ["Zhao Wei"], 2020, "MICCAI Workshop",
        "We swap backbone networks and tune augmentation to climb the public "
        "leaderboard for lung nodule classification. Results are reported only on the "
        "public benchmark without external validation, without stratified analysis and "
        "without cross-device evaluation."),
    _mock(
        "A survey of natural language processing methods for clinical text "
        "de-identification",
        ["O'Neil Karen", "付强"], 2020, "J Biomed Inform",
        "This survey reviews natural language processing (NLP) approaches for clinical "
        "text de-identification, covering token classification, context modeling and "
        "text generation for protected health information removal. No medical imaging "
        "component is involved."),
    _mock(
        "Benchmarking transformer backbones for chest report generation on an "
        "open leaderboard",
        ["Ito Yuki"], 2024, "ACL Workshop",
        "We benchmark several transformer backbone architectures for chest report "
        "generation and publish an open leaderboard. The study focuses on text "
        "generation metrics and does not discuss imaging diagnosis, external "
        "validation or domain shift."),
    _mock(
        "Radiomics nomogram for gastric cancer staging on a single-center "
        "retrospective cohort",
        ["黄磊", "Fischer A."], 2019, "Abdominal Radiology",
        "We build a radiomics nomogram for gastric cancer staging using a "
        "single-center retrospective cohort of contrast-enhanced CT. The model is "
        "internally validated with bootstrapping only; no external validation is "
        "performed. The task is tumor staging rather than lung nodule detection."),
    _mock(
        "Deep learning triage of chest radiographs in the emergency department",
        ["Brown Sarah"], 2018, "Annals of Emergency Medicine",
        "A convolutional classifier triages routine chest radiographs in an emergency "
        "department to prioritize worklist ordering. Throughput improved by 8 percent "
        "in a simulated workflow. This 2018 paper predates the review time window."),
    _mock(
        "Calibrating lung nodule detectors under low-dose CT protocol shift: "
        "a multi-vendor external validation",
        ["Zhou Qin", "Martin D.", "Kumar S."], 2024, "Medical Physics",
        "Low-dose CT protocols differ across vendors and reconstruction kernels, which "
        "induces domain shift for lung nodule detection. We perform external validation "
        "on data from five centers and multiple CT scanner vendors, and report "
        "stratified sensitivity by nodule size, dose level and manufacturer. Physical "
        "acquisition shift is quantified separately from patient case-mix differences, "
        "and cross-scanner calibration curves are provided for clinical deployment."),
    _mock(
        "Prospective evaluation of an AI nodule reader across CT scanner generations "
        "in lung cancer screening",
        ["Andersson M.", "李嘉"], 2025, "Radiology: Artificial Intelligence",
        "In a prospective lung cancer screening workflow we evaluate an AI nodule reader "
        "across three CT scanner generations at two centers. The system abstains on "
        "low-confidence scans and forwards them to radiologists, so rejection handling "
        "and worklist handover were designed together with the detection model. "
        "Multi-center external validation showed that single-center retrospective "
        "testing had overestimated sensitivity, and that scanner domain shift and "
        "disease spectrum shift must be corrected for before deployment."),
    _mock(
        "Disentangling acquisition shift from case-mix shift in low-dose CT screening: "
        "a stratified re-analysis",
        ["Gupta A.", "Tanaka H."], 2022, "Journal of Medical Imaging",
        "We re-analyze published low-dose CT screening results to separate acquisition "
        "domain shift caused by scanner hardware from disease spectrum shift caused by "
        "population differences. A stratified re-analysis across four cohorts shows that "
        "reported performance drops decompose into a physical component and a case-mix "
        "component, and that external validation on a single center does not generalize. "
        "We recommend paired phantoms plus clinical stratification for domain shift "
        "attribution in nodule detection."),
]


def search_mock(topic, years, limit):
    return [dict(p) for p in MOCK_PAPERS[:max(1, min(len(MOCK_PAPERS), limit))]]


def search(source, task):
    """按来源执行检索，返回文献列表；失败抛 SourceError。"""
    limit = min(50, max(10, task["max_papers"] * 2))
    if source == "openalex":
        return search_openalex(task["topic"], task["years"], limit)
    if source == "semantic_scholar":
        return search_semantic_scholar(task["topic"], task["years"], limit)
    if source == "pubmed":
        return search_pubmed(task["topic"], task["years"], limit)
    if source == "mock":
        return search_mock(task["topic"], task["years"], limit)
    raise SourceError("未知检索源：%s" % source)


PUBMED_PACING = 0.4   # NCBI 要求连续请求之间至少间隔 0.4 秒


def _pubmed_term(query, years):
    return "(%s) AND (%d/01/01[Date - Publication] : %d/12/31[Date - Publication])" % (
        query, years[0], years[1])


def _pubmed_search_url(query, years, limit, retstart=None):
    params = [
        ("db", "pubmed"),
        ("term", _pubmed_term(query, years)),
        ("retmode", "json"),
        ("retmax", limit),
    ]
    if retstart is not None:
        params.append(("retstart", retstart))
        params.append(("sort", "relevance"))
    return "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esearch.fcgi?" + urllib.parse.urlencode(params)


def _pubmed_search_page(query, years, limit, retstart=None):
    """ESearch one batch; returns (result, url, ids)."""
    search_url = _pubmed_search_url(query, years, limit, retstart=retstart)
    found = _get_json(search_url)
    if not isinstance(found, dict):
        raise SourceError("PubMed 返回内容格式异常")
    result = found.get("esearchresult", {}) or {}
    if found.get("error") or result.get("ERROR") or result.get("errorlist"):
        raise SourceError("PubMed 检索表达式或服务返回错误：%s" % (
            found.get("error") or result.get("ERROR") or result.get("errorlist")))
    if not isinstance(result.get("idlist"), list):
        raise SourceError("PubMed 返回缺少 idlist，不能当作零命中")
    ids = [str(i) for i in (result.get("idlist") or []) if i not in (None, "")]
    return result, search_url, ids


def _pubmed_fetch(ids):
    """EFetch one batch of PMIDs (retried as the same request) and parse it.

    DOI is read only from ``PubmedArticle/PubmedData/ArticleIdList`` so DOIs
    that appear inside reference lists can never be assigned to the article.
    """
    params = urllib.parse.urlencode({"db": "pubmed", "id": ",".join(ids), "retmode": "xml"})
    fetch_url = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/efetch.fcgi?" + params
    time.sleep(PUBMED_PACING)
    raw = _get_bytes(fetch_url)
    try:
        root = ET.fromstring(raw)
    except ET.ParseError:
        raise SourceError("PubMed 返回内容不是合法 XML")
    if root.find(".//ERROR") is not None:
        raise SourceError("PubMed 元数据获取失败")

    def txt(node):
        return "".join(node.itertext()).strip() if node is not None else ""

    papers = []
    for article in root.findall(".//PubmedArticle"):
        pmid = article.findtext("./MedlineCitation/PMID") or article.findtext(".//MedlineCitation/PMID") or ""
        title = txt(article.find(".//ArticleTitle"))
        authors = [" ".join(filter(None, [a.findtext("LastName"), a.findtext("ForeName")])) or
                   a.findtext("CollectiveName", "") for a in article.findall(".//Author")]
        date = article.find(".//JournalIssue/PubDate")
        match = re.search(r"\d{4}", txt(date))
        doi = next((txt(i) for i in article.findall("./PubmedData/ArticleIdList/ArticleId")
                    if i.get("IdType") == "doi"), "")
        paper = _paper(title, authors, int(match[0]) if match else None,
                       " ".join(txt(a) for a in article.findall(".//Abstract/AbstractText")),
                       txt(article.find(".//Journal/Title")), "pubmed",
                       "https://pubmed.ncbi.nlm.nih.gov/%s/" % pmid, doi)
        paper["pmid"] = pmid
        paper["source_record_xml"] = ET.tostring(article, encoding="unicode")
        papers.append(paper)
    if not papers:
        raise SourceError("PubMed 有命中编号但未返回可解析题录，不能作为空结果通过")
    return papers, fetch_url


def search_pubmed(query, years, limit, *, retstart=None):
    """Single-page PubMed search (unchanged public behaviour).

    ``retstart`` is optional so the paginator can request a later batch while
    the existing three-argument call still requests page 1.
    """
    result, search_url, ids = _pubmed_search_page(query, years, limit, retstart=retstart)
    if not ids:
        return SearchResults(total=result.get("count"), limit=limit,
                             request_url=search_url, response_meta=result)
    papers, _fetch_url = _pubmed_fetch(ids)
    return SearchResults(papers, total=result.get("count"), limit=limit,
                         request_url=search_url, response_meta=result)


# ---------------------------------------------------------------------------
# Sequential pagination (shared by all real sources)
# ---------------------------------------------------------------------------

MAX_PAGES = 4


def _task_years(task):
    years = (task or {}).get("years")
    try:
        return int(years[0]), int(years[1])
    except (TypeError, ValueError, IndexError, KeyError):
        return 1900, 2100


def _paper_identity(paper, source):
    """Stable per-source identifier used to catch repeated pages."""
    record = paper.get("source_record")
    record = record if isinstance(record, dict) else {}
    if source == "openalex":
        key = record.get("id") or paper.get("url") or paper.get("doi")
    elif source == "semantic_scholar":
        key = record.get("paperId") or paper.get("doi") or paper.get("url")
    elif source == "pubmed":
        key = paper.get("pmid") or paper.get("url")
    else:
        key = paper.get("doi") or paper.get("url")
    if key:
        return str(key)
    title = (paper.get("title") or "").strip().lower()
    if title:
        return "%s|%s|%s" % (title, paper.get("year"), paper.get("source"))
    return None


def _split_new(papers, seen, source):
    """Drop identifiers already seen; returns (new papers, duplicate keys)."""
    new, duplicates = [], []
    for paper in papers:
        key = _paper_identity(paper, source)
        if key is not None and key in seen:
            duplicates.append(key)
            continue
        if key is not None:
            seen.add(key)
        new.append(paper)
    return new, duplicates


def _page_entry(source, page_no, query, request_url, requested, retrieved,
                status="ok", error=None, cursor=None, offset=None,
                retstart=None, **extra):
    entry = {
        "page": page_no,
        "source": source,
        "query": query,
        "request_url": request_url,
        "requested_count": requested,
        "retrieved_count": retrieved,
        "status": status,
    }
    if cursor is not None:
        entry["cursor"] = cursor
    if offset is not None:
        entry["offset"] = offset
    if retstart is not None:
        entry["retstart"] = retstart
    if error:
        entry["error"] = error
    entry.update(extra)
    return entry


def _error_entry(page_no, error, request_url="", kind="page_failure", **extra):
    entry = {
        "page": page_no,
        "error": error,
        "request_url": request_url,
        "kind": kind,
    }
    entry.update(extra)
    return entry


def _page_stop(on_page, papers, page):
    """Call the optional per-page policy hook after a successful batch.

    ``on_page`` receives a snapshot of the cumulative records and the audit
    page entry.  A non-empty return value is the reason to stop paginating.
    Exceptions intentionally propagate: a broken policy callback must not be
    disguised as a provider network failure.
    """
    if on_page is None or page.get("status") != "ok":
        return None
    reason = on_page(list(papers), page)
    return str(reason) if reason else None


def _apply_policy_stop(result, reason):
    """Keep all records and annotate a policy stop on page and envelope."""
    if result.pages:
        result.pages[-1]["policy_stop"] = reason
    audit_pages = result.response_meta.get("pages")
    if audit_pages:
        audit_pages[-1]["policy_stop"] = reason
    result.response_meta["stop_reason"] = reason
    result.response_meta["stopped_by_policy"] = True


def _paged_result(source, topic, years, papers, pages, errors, total,
                  page_size, max_pages, exhausted):
    request_limit = page_size * max_pages
    successful = sum(1 for p in pages if p.get("status") == "ok")
    if not pages:
        status = "failed"
    elif not papers and successful == 0:
        status = "failed"          # page 1 failed: nothing usable was retrieved
    elif errors:
        status = "partial"
    else:
        status = "complete"
    request_urls = []
    for page in pages:
        for key in ("request_url", "fetch_url"):
            value = page.get(key)
            if value and value not in request_urls:
                request_urls.append(value)
    response_meta = {
        "source": source,
        "query": topic,
        "years": list(years),
        "total": total,
        "page_size": page_size,
        "requested_pages": max_pages,
        "actual_pages": len(pages),
        "successful_pages": successful,
        "exhausted": bool(exhausted),
        "status": status,
        "errors": [dict(e) for e in errors],
        "request_urls": list(request_urls),
        "request_limit": request_limit,
        "pages": [dict(p) for p in pages],
        "demo": source == "mock",
    }
    result = SearchResults(
        papers,
        total=total,
        limit=request_limit,
        request_url=request_urls[0] if request_urls else "",
        response_meta=response_meta,
        pages=pages,
        errors=errors,
        status=status,
        exhausted=bool(exhausted),
        request_urls=request_urls,
    )
    result.source = source
    result.query = topic
    result.page_size = page_size
    return result


def _paginate_openalex(topic, years, page_size, max_pages, on_page=None):
    papers, pages, errors = [], [], []
    seen_ids, seen_cursors = set(), {"*"}
    total = None
    exhausted = False
    stop_reason = None
    cursor = "*"
    basic_mode = False

    for page_no in range(1, max_pages + 1):
        url = _openalex_url(topic, years, page_size, cursor=cursor)
        fallback = {}
        def basic_url(value):
            parsed = urllib.parse.urlsplit(value)
            params = dict(urllib.parse.parse_qsl(parsed.query))
            params.pop("cursor", None)
            params["page"] = page_no
            return urllib.parse.urlunsplit(parsed._replace(query=urllib.parse.urlencode(params)))
        if basic_mode:
            url = basic_url(url)
        try:
            try:
                data = _get_json(url)
            except Exception as exc:
                # OpenAlex allows basic paging of the first 10,000 records
                # without the key now required after cursor page 1. Resume
                # at this same page number; never repeat the first page.
                if page_no > 1 and not basic_mode and "401" in friendly_error(exc):
                    fallback = {"cursor_attempt_url": url, "cursor_attempt_error": friendly_error(exc)}
                    basic_mode = True
                    url = basic_url(url)
                    data = _get_json(url)
                else:
                    raise
            page_papers, meta = _parse_openalex(data)
        except Exception as exc:            # noqa: BLE001 page failure is audited, not fatal
            message = friendly_error(exc)
            pages.append(_page_entry("openalex", page_no, topic, url,
                                     page_size, 0, "error", message, cursor=cursor))
            errors.append(_error_entry(page_no, message, url, cursor=cursor))
            break

        page_total = _as_int(meta.get("count"))
        if page_total is not None:
            total = page_total

        new_papers, duplicates = _split_new(page_papers, seen_ids, "openalex")
        next_cursor = meta.get("next_cursor")
        if duplicates and not new_papers:
            message = "OpenAlex 第 %d 页返回了已出现过的文献标识，已丢弃重复项" % page_no
            pages.append(_page_entry(
                "openalex", page_no, topic, url, page_size, len(new_papers),
                "error", message, cursor=cursor, next_cursor=next_cursor))
            errors.append(_error_entry(
                page_no, message, url, kind="repeated_identifiers",
                cursor=cursor, duplicate_count=len(duplicates)))
            papers.extend(new_papers)
            break

        pages.append(_page_entry(
            "openalex", page_no, topic, url, page_size, len(page_papers),
            "ok", cursor=None if basic_mode else cursor, next_cursor=next_cursor,
            paging="basic" if basic_mode else "cursor", offset=(page_no - 1) * page_size,
            duplicate_count=len(duplicates), **fallback))
        papers.extend(page_papers)

        if not page_papers:
            exhausted = True
            break
        if total is not None and len(papers) >= total:
            exhausted = True
            break
        if basic_mode:
            if len(page_papers) < page_size:
                exhausted = True
                break
            stop_reason = _page_stop(on_page, papers, pages[-1])
            if stop_reason:
                break
            continue
        if not next_cursor:
            exhausted = True
            break
        stop_reason = _page_stop(on_page, papers, pages[-1])
        if stop_reason:
            break
        if next_cursor in seen_cursors:
            message = "OpenAlex 游标未前进（%s），停止分页以避免重复" % next_cursor
            errors.append(_error_entry(
                page_no, message, url, kind="non_advancing_cursor",
                cursor=cursor, next_cursor=next_cursor))
            break
        seen_cursors.add(next_cursor)
        cursor = next_cursor

    result = _paged_result("openalex", topic, years, papers, pages, errors, total,
                           page_size, max_pages, exhausted)
    if stop_reason:
        _apply_policy_stop(result, stop_reason)
    return result


def _paginate_semantic_scholar(topic, years, page_size, max_pages, on_page=None):
    papers, pages, errors = [], [], []
    seen_ids = set()
    total = None
    exhausted = False
    stop_reason = None
    offset = 0

    for page_no in range(1, max_pages + 1):
        url = _semantic_url(topic, years, page_size, offset=offset)
        try:
            data = _get_json(url)
            page_papers = _parse_semantic(data)
        except Exception as exc:            # noqa: BLE001 page failure is audited, not fatal
            message = friendly_error(exc)
            pages.append(_page_entry("semantic_scholar", page_no, topic, url,
                                     page_size, 0, "error", message, offset=offset))
            errors.append(_error_entry(page_no, message, url, offset=offset))
            break

        page_total = _as_int(data.get("total"))
        if page_total is not None:
            total = page_total

        new_papers, duplicates = _split_new(page_papers, seen_ids, "semantic_scholar")
        if duplicates and not new_papers:
            message = "Semantic Scholar 第 %d 页返回了已出现过的论文标识，已丢弃重复项" % page_no
            pages.append(_page_entry(
                "semantic_scholar", page_no, topic, url, page_size,
                len(new_papers), "error", message, offset=offset))
            errors.append(_error_entry(
                page_no, message, url, kind="repeated_identifiers",
                offset=offset, duplicate_count=len(duplicates)))
            papers.extend(new_papers)
            break

        pages.append(_page_entry(
            "semantic_scholar", page_no, topic, url, page_size,
            len(page_papers), "ok", offset=offset))
        papers.extend(page_papers)

        if not page_papers:
            exhausted = True
            break
        if total is not None and offset + len(page_papers) >= total:
            exhausted = True
            break
        # Never advance from a short page: offset+50 could skip records.
        if len(page_papers) < page_size:
            exhausted = True
            break

        stop_reason = _page_stop(on_page, papers, pages[-1])
        if stop_reason:
            break

        next_value = data.get("next")
        next_offset = _as_int(next_value)
        if next_offset is not None:
            if next_offset <= offset:
                message = "Semantic Scholar next offset 未前进（%s），停止分页" % next_value
                errors.append(_error_entry(
                    page_no, message, url, kind="non_advancing_offset",
                    offset=offset, next=next_value))
                break
            offset = next_offset
        else:
            offset += page_size

    result = _paged_result("semantic_scholar", topic, years, papers, pages,
                           errors, total, page_size, max_pages, exhausted)
    if stop_reason:
        _apply_policy_stop(result, stop_reason)
    return result


def _paginate_pubmed(topic, years, page_size, max_pages, on_page=None):
    papers, pages, errors = [], [], []
    seen_pmids = set()
    total = None
    exhausted = False
    stop_reason = None
    retstart = 0

    for page_no in range(1, max_pages + 1):
        if page_no > 1:
            time.sleep(PUBMED_PACING)
        search_url = _pubmed_search_url(topic, years, page_size, retstart=retstart)
        try:
            result, search_url, ids = _pubmed_search_page(
                topic, years, page_size, retstart=retstart)
        except Exception as exc:            # noqa: BLE001 page failure is audited, not fatal
            message = friendly_error(exc)
            pages.append(_page_entry("pubmed", page_no, topic, search_url,
                                     page_size, 0, "error", message,
                                     retstart=retstart))
            errors.append(_error_entry(page_no, message, search_url,
                                       retstart=retstart))
            break

        page_total = _as_int(result.get("count"))
        if page_total is not None:
            total = page_total
        if not ids:
            pages.append(_page_entry("pubmed", page_no, topic, search_url,
                                     page_size, 0, "ok", retstart=retstart,
                                     pmids=[]))
            exhausted = True
            break

        new_ids, repeated = [], []
        for pmid in ids:
            if pmid in seen_pmids:
                repeated.append(pmid)
            else:
                seen_pmids.add(pmid)
                new_ids.append(pmid)

        fetch_url = ""
        page_papers = []
        fetch_error = None
        if new_ids:
            try:
                page_papers, fetch_url = _pubmed_fetch(ids)
            except Exception as exc:        # noqa: BLE001 page failure is audited
                fetch_error = friendly_error(exc)
        duplicate_error = None
        if repeated and not new_ids:
            duplicate_error = "PubMed 第 %d 页返回了已出现过的 PMID（%s），已丢弃重复项" % (
                page_no, ",".join(repeated[:5]))
        if fetch_error:
            message = fetch_error
            pages.append(_page_entry(
                "pubmed", page_no, topic, search_url, page_size,
                len(page_papers), "error", message, retstart=retstart,
                fetch_url=fetch_url, pmids=new_ids))
            errors.append(_error_entry(page_no, message, search_url,
                                       retstart=retstart,
                                       fetch_url=fetch_url))
        elif duplicate_error:
            pages.append(_page_entry(
                "pubmed", page_no, topic, search_url, page_size,
                len(page_papers), "error", duplicate_error,
                retstart=retstart, fetch_url=fetch_url, pmids=new_ids,
                repeated_pmids=repeated))
            errors.append(_error_entry(page_no, duplicate_error, search_url,
                                       kind="repeated_identifiers",
                                       retstart=retstart,
                                       duplicate_count=len(repeated)))
        else:
            pages.append(_page_entry(
                "pubmed", page_no, topic, search_url, page_size,
                len(page_papers), "ok", retstart=retstart,
                fetch_url=fetch_url, pmids=ids, duplicate_count=len(repeated)))
        papers.extend(page_papers)

        if repeated and not new_ids:
            break
        if fetch_error:
            break
        if total is not None and retstart + len(ids) >= total:
            exhausted = True
            break
        if len(ids) < page_size:
            exhausted = True
            break

        stop_reason = _page_stop(on_page, papers, pages[-1])
        if stop_reason:
            break

        retstart += page_size

    result = _paged_result("pubmed", topic, years, papers, pages, errors, total,
                           page_size, max_pages, exhausted)
    if stop_reason:
        _apply_policy_stop(result, stop_reason)
    return result


def _paginate_mock(topic, years, page_size, max_pages):
    request_limit = page_size * max_pages
    papers = [dict(p) for p in MOCK_PAPERS[:request_limit]]
    url = "mock://local/search?" + urllib.parse.urlencode({"topic": topic})
    page = _page_entry("mock", 1, topic, url, page_size, len(papers),
                       "ok", demo=True)
    result = _paged_result("mock", topic, years, papers, [page], [], len(papers),
                           page_size, max_pages, True)
    result.response_meta["demo"] = True
    result.response_meta["note"] = "演示数据（mock），不是真实检索结果"
    result.demo = True
    return result


def search_paginated(source, task, *, page_size=50, max_pages=MAX_PAGES,
                     query=None, on_page=None):
    """Sequential pagination with partial-success auditing.

    Up to ``max_pages`` (hard cap 4) distinct batches of ``page_size`` (hard
    cap 50) are fetched per real source.  A failed later page keeps every
    earlier record and marks the result ``partial``; page 1 is never retried
    as a substitute for a later page, and search parameters never change
    mid-pagination.

    ``on_page(cumulative_papers, page_dict)`` is an optional policy hook
    called once after each non-terminal successful batch.  Returning a
    non-empty reason stops pagination while keeping all records; the last
    page is tagged ``policy_stop`` and the envelope gets ``stop_reason`` /
    ``stopped_by_policy``.  Natural source exhaustion is checked first, so it
    is never reported as a policy stop, and callback exceptions propagate.

    Returns a :class:`SearchResults` list with ``pages``, ``errors``,
    ``status`` (complete/partial/failed), ``exhausted``, ``total`` and
    ``request_urls``.  ``mock`` remains explicit opt-in and is never used as
    a fallback.
    """
    task = task or {}
    topic = query if query is not None else (task.get("topic") or "")
    years = _task_years(task)
    try:
        page_size = int(page_size)
    except (TypeError, ValueError):
        page_size = 50
    try:
        max_pages = int(max_pages)
    except (TypeError, ValueError):
        max_pages = MAX_PAGES
    page_size = max(1, min(50, page_size))
    max_pages = max(1, min(MAX_PAGES, max_pages))

    if source == "openalex":
        return _paginate_openalex(topic, years, page_size, max_pages,
                                  on_page=on_page)
    if source == "semantic_scholar":
        return _paginate_semantic_scholar(topic, years, page_size, max_pages,
                                          on_page=on_page)
    if source == "pubmed":
        return _paginate_pubmed(topic, years, page_size, max_pages,
                                on_page=on_page)
    if source == "mock":
        return _paginate_mock(topic, years, page_size, max_pages)
    raise SourceError("未知检索源：%s" % source)
