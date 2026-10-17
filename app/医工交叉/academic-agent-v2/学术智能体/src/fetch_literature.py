# -*- coding: utf-8 -*-
"""fetch_literature：按任务卡 sources 顺序检索文献，并把「原始结果」落盘。

依赖 sources.py（openalex / semantic_scholar / mock）。
- 单源失败不中断整体：记录 status=请求失败 与友好原因，继续下一个来源；
- 每个来源的原始返回都写入 outputs/<run>/raw_<source>.json，保证可复查。
"""
import json
import os

from . import sources
from .parse_request import _parse_years


def _normalize(task):
    lo, hi = _parse_years(task.get("years"))
    norm = dict(task)
    norm["years"] = (lo, hi)
    norm["max_papers"] = int(task.get("max_papers") or 25)
    return norm


def fetch(task, outdir=None):
    """返回 (records, papers)。records 记录每个来源的状态，papers 为合并后的原始文献。"""
    norm = _normalize(task)
    records, papers = [], []
    if outdir:
        os.makedirs(outdir, exist_ok=True)
    for source in task.get("sources") or []:
        source = str(source)
        try:
            got = sources.search(source, norm)
            status, detail = ("命中" if got else "未命中"), ""
        except Exception as exc:  # noqa: BLE001 单源失败只标注，不中断
            got, status, detail = [], "请求失败", sources.friendly_error(exc)
        if outdir:
            path = os.path.join(outdir, "raw_%s.json" % source)
            with open(path, "w", encoding="utf-8") as fh:
                json.dump({"source": source, "status": status, "detail": detail,
                           "count": len(got), "papers": got}, fh, ensure_ascii=False, indent=2)
        papers.extend(got)
        records.append({"source": source, "status": status,
                        "count": len(got), "detail": detail})
    return records, papers


def summary_line(records):
    chunks = []
    for r in records:
        if r["status"] == "命中":
            chunks.append("%s 命中 %d 篇" % (r["source"], r["count"]))
        elif r["status"] == "未命中":
            chunks.append("%s 未命中" % r["source"])
        else:
            chunks.append("%s 请求失败（%s）" % (r["source"], r["detail"]))
    return " | ".join(chunks) if chunks else "（未配置检索源）"
