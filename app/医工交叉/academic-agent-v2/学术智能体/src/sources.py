# -*- coding: utf-8 -*-
"""检索源适配器：openalex / semantic_scholar / mock。

约束（见任务卡要求）：
  - 只接这三个来源，按任务卡 sources 顺序逐个查询；
  - 单次请求 10 秒超时、最多重试 2 次（合计最多 3 次尝试）；
  - 任一来源失败只抛 SourceError（友好中文说明），由调用方标注「请求失败」后继续下一个来源。
"""
import json
import re
import socket
import time
import urllib.error
import urllib.parse
import urllib.request

TIMEOUT = 10       # 单次请求超时（秒）
MAX_RETRY = 2      # 最多重试 2 次
UA = "academic-agent/0.1 (local MVP; +python-urllib)"


class SourceError(Exception):
    """检索失败（已转成友好中文说明）。"""


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


def search_openalex(topic, years, limit):
    lo, hi = years
    query = urllib.parse.urlencode({
        "search": topic,
        "filter": "from_publication_date:%d-01-01,to_publication_date:%d-12-31" % (lo, hi),
        "per-page": max(1, min(50, limit)),
    })
    data = _get_json("https://api.openalex.org/works?" + query)
    out = []
    for w in (data.get("results") or []):
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
    return out


def search_semantic_scholar(topic, years, limit):
    query = urllib.parse.urlencode({
        "query": topic,
        "limit": max(1, min(100, limit)),
        "fields": "title,authors,year,abstract,venue,externalIds,url",
    })
    data = _get_json("https://api.semanticscholar.org/graph/v1/paper/search?" + query)
    out = []
    for p in (data.get("data") or []):
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
    return out


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
    if source == "mock":
        return search_mock(task["topic"], task["years"], limit)
    raise SourceError("未知检索源：%s" % source)
