# -*- coding: utf-8 -*-
"""verify_citations：核验综述里的引用是否都能回查到文献表。

核验规则（对应 AGENTS.md / 提示词D）：
  1) 正文中出现的 [n] 必须能在文献表找到对应编号，否则判为「虚假引用」；
  2) 正文中出现的 DOI（10.xxxx/...）若不等于任何文献表条目的标识符，也判为虚假引用；
  3) 文献表条目若缺标题或缺链接（DOI/URL），核验失败；
  4) 结果同时给出未被引用的文献（覆盖缺口）与通过/失败结论。
"""
import re

CITE_RE = re.compile(r"\[(\d+)\]")
DOI_RE = re.compile(r"10\.\d{4,9}/[-._;()/:A-Za-z0-9]+")
REF_SECTION_RE = re.compile(r"^#{1,6}\s*三、参考文献.*$", re.M)


def _body_only(review_md):
    """去掉文末参考文献小节，避免把文献列表里的 [n] 当作正文引用。"""
    m = REF_SECTION_RE.search(review_md or "")
    return review_md[:m.start()] if m else (review_md or "")


def verify(review_md, refs):
    """返回核验报告 dict。refs: [{n, title, identifier}, ...]。"""
    review_md = review_md or ""
    ref_numbers = set()
    ref_by_n = {}
    for r in refs or []:
        try:
            n = int(r.get("n"))
        except (TypeError, ValueError):
            continue
        ref_numbers.add(n)
        ref_by_n[n] = r

    empty_title = [int(r["n"]) for r in refs or []
                   if not str(r.get("title") or "").strip()]
    empty_link = [int(r["n"]) for r in refs or []
                  if not str(r.get("identifier") or "").strip()]

    body = _body_only(review_md)
    cited = sorted({int(x) for x in CITE_RE.findall(body)})
    fake_citations = sorted(n for n in cited if n not in ref_numbers)
    uncited = sorted(ref_numbers - set(cited))

    known_dois = {str(r.get("identifier") or "").strip().lower() for r in refs or []}
    fake_dois = sorted({d for d in DOI_RE.findall(body)
                        if d.lower() not in known_dois})

    checks = [
        {"name": "引用编号可回查", "ok": not fake_citations,
         "detail": ("全部 %d 个引用编号均可回查" % len(cited)) if not fake_citations
                   else "存在无法回查的引用编号：%s" % "、".join("[%d]" % n for n in fake_citations)},
        {"name": "无编造 DOI", "ok": not fake_dois,
         "detail": "正文未出现文献表之外的 DOI" if not fake_dois
                   else "正文出现文献表之外 DOI：%s" % "、".join(fake_dois)},
        {"name": "文献表标题完整", "ok": not empty_title,
         "detail": "所有条目均有标题" if not empty_title
                   else "以下条目缺少标题：编号 %s" % "、".join(map(str, empty_title))},
        {"name": "文献表链接完整", "ok": not empty_link,
         "detail": "所有条目均有 DOI/URL" if not empty_link
                   else "以下条目缺少 DOI/URL：编号 %s" % "、".join(map(str, empty_link))},
    ]
    ok = all(c["ok"] for c in checks)
    return {
        "ok": ok,
        "checks": checks,
        "cited": cited,
        "fake_citations": fake_citations,
        "fake_dois": fake_dois,
        "empty_title": empty_title,
        "empty_link": empty_link,
        "uncited": uncited,
        "n_refs": len(ref_numbers),
    }


def report_text(report, review_path="review.md", refs_path="references.json"):
    lines = ["# 引用核验报告", ""]
    lines.append("结论：%s" % ("通过 ✅" if report["ok"] else "未通过 ❌"))
    lines.append("")
    lines.append("核验对象：正文 = %s，文献表 = %s，共 %d 条文献。"
                 % (review_path, refs_path, report["n_refs"]))
    lines.append("")
    lines.append("| 检查项 | 结果 | 说明 |")
    lines.append("| --- | --- | --- |")
    for c in report["checks"]:
        lines.append("| %s | %s | %s |" % (c["name"], "通过" if c["ok"] else "失败", c["detail"]))
    lines.append("")
    if report["fake_citations"]:
        lines.append("虚假引用编号：%s（这些编号在文献表中不存在）"
                     % "、".join("[%d]" % n for n in report["fake_citations"]))
    if report["fake_dois"]:
        lines.append("文献表之外的 DOI：%s" % "、".join(report["fake_dois"]))
    if report["uncited"]:
        lines.append("未被正文引用的文献（覆盖缺口）：%s"
                     % "、".join("[%d]" % n for n in report["uncited"]))
    if report["ok"]:
        lines.append("说明：核验通过仅代表「引用可回查」，不代表内容正确；"
                     "请按 scripts/spotcheck.md 进行人工抽查。")
    return "\n".join(lines) + "\n"
