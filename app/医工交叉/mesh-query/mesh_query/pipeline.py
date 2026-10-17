"""从一句话到检索式: 切分 -> 选词 -> 拼装 -> 报告与映射记录。

设计要点:
- 一句话只做一次切分, 不支持多问题批量 (需求约束);
- 英文: 最长词窗优先, 依次尝试 词形精确 -> 去结构词精确 -> 词集相等;
- 中文: 按 data/zh_map.json 贪心最长匹配得到英文提示词, 再用本地索引二次校验,
        校验不过一律标【待核】;
- 命中不确定的片段不会写进 [MeSH Terms] 字段, 只保留自由词, 避免编造主题词。
"""

from __future__ import annotations

import json
import re
import uuid
from dataclasses import dataclass, field
from datetime import datetime

from . import compose as compose_layer
from . import paths, zhmap
from .build_index import normalize
from .compose import ComposeOptions, mesh_tag
from .lexicon import BASIS_LABEL, Lexicon, content_tokens
from .stopwords import EN_STOP

MAX_PHRASE = 6

# 英文词 / 数字 / 中文(可含数字) 运行段
TOKEN_RE = re.compile(r"[0-9]*(?:[一-鿿]+[0-9]*)+|[A-Za-z][A-Za-z0-9'’\-]*|\d+")

BASIS_ORDER = ("exact", "variant", "dropped_stop", "token_set", "subset", "loose")


# ---------------------------------------------------------------- 数据结构
@dataclass
class Chunk:
    text: str                     # 展示用原文片段
    lang: str                     # en / zh
    status: str                   # matched / unmatched / skip
    phrase: str                   # 参与选词的词形
    note: str = ""                # 切分阶段的说明
    mapped_en: str = ""           # 中文 -> 英文提示词
    candidates: list[dict] = field(default_factory=list)
    chosen: dict | None = None


# ---------------------------------------------------------------- 切分
def _segments(text: str) -> list[dict]:
    """按语言切成连续段; 英文段记录每个 token 的原文偏移, 中文段保留原文。"""
    segs: list[dict] = []
    for match in TOKEN_RE.finditer(text):
        token = match.group()
        lang = "zh" if any("一" <= ch <= "鿿" for ch in token) else "en"
        item = {"tok": token, "start": match.start(), "end": match.end()}
        if segs and segs[-1]["lang"] == lang:
            segs[-1]["end"] = match.end()
            segs[-1]["tokens"].append(item)
        else:
            segs.append(
                {"lang": lang, "start": match.start(), "end": match.end(), "tokens": [item]}
            )
    for seg in segs:
        # 自然语言字段要贴合用户原句, 所以按原文偏移取, 不重新拼 token
        seg["text"] = text[seg["start"] : seg["end"]]
        for item in seg["tokens"]:
            item["rel_start"] = item["start"] - seg["start"]
            item["rel_end"] = item["end"] - seg["start"]
    return segs


def _try_window(lex: Lexicon, tokens: list[str]) -> tuple[str, str] | None:
    """尝试一个词窗, 返回 (依据, 命中词形) 或 None。"""
    phrase = " ".join(tokens)
    if lex.has_phrase(phrase):
        return "exact", phrase

    wanted = content_tokens(tokens)
    if wanted and wanted != tokens and lex.has_phrase(" ".join(wanted)):
        return "dropped_stop", " ".join(wanted)
    if len(wanted) >= 2 and lex.token_match(tokens):
        return "token_set", phrase
    return None


def _split_en(tokens: list[dict], lex: Lexicon) -> tuple[list[dict], list[str]]:
    spans: list[dict] = []
    skipped: list[str] = []
    total = len(tokens)
    i = 0
    while i < total:
        hit = None
        hit_n = 0
        for n in range(min(MAX_PHRASE, total - i), 1, -1):  # 先试 2 词以上
            res = _try_window(lex, [t["tok"] for t in tokens[i : i + n]])
            if res:
                hit, hit_n = res, n
                break
        if not hit:
            token = tokens[i]["tok"]
            if _is_stop(token):
                skipped.append(token)
                i += 1
                continue
            res = _try_window(lex, [token])
            if res:
                hit, hit_n = res, 1
        if hit:
            basis, phrase = hit
            a, b = i, i + hit_n
            while b - 1 > a and _is_stop(tokens[b - 1]["tok"]):  # 裁掉尾部结构词
                b -= 1
            while a < b - 1 and _is_stop(tokens[a]["tok"]):      # 裁掉头部结构词
                a += 1
            # 裁完若与命中词形一致, 说明结构词都在窗口边缘, 依据应报"完全一致"
            if basis == "dropped_stop" and normalize(
                " ".join(t["tok"] for t in tokens[a:b])
            ) == normalize(phrase):
                basis = "exact"
            spans.append(
                {
                    "status": "matched",
                    "phrase": phrase,
                    "note": _basis_note(basis),
                    "rel_start": tokens[a]["rel_start"],
                    "rel_end": tokens[b - 1]["rel_end"],
                }
            )
            i += hit_n
        else:
            spans.append(
                {
                    "status": "unmatched",
                    "phrase": tokens[i]["tok"],
                    "note": "本地词表未命中",
                    "rel_start": tokens[i]["rel_start"],
                    "rel_end": tokens[i]["rel_end"],
                }
            )
            i += 1
    return spans, skipped


def _is_stop(token: str) -> bool:
    return token.lower() in EN_STOP


def _basis_note(basis: str) -> str:
    return BASIS_LABEL.get(basis, basis)


def _split_zh(text: str, lex: Lexicon, entries: dict[str, str]) -> tuple[list[dict], list[str]]:
    """中文: 贪心最长匹配提示表; 未命中的连续中文字串标【待核】。"""
    keys = sorted(entries, key=len, reverse=True)
    skips = sorted(zhmap.SKIP, key=len, reverse=True)
    spans: list[dict] = []
    skipped: list[str] = []
    pending: list[str] = []
    i = 0

    def flush() -> None:
        if pending:
            joined = "".join(pending)
            spans.append(
                {"status": "unmatched", "phrase": joined, "text": joined, "note": "未在中文提示表中找到"}
            )
            pending.clear()

    while i < len(text):
        ch = text[i]
        if ch.isspace() or ch in zhmap.PUNCT:
            i += 1
            continue
        hit_key = next((k for k in keys if text.startswith(k, i)), None)
        if hit_key:
            flush()
            spans.append(
                {
                    "status": "matched",
                    "phrase": hit_key,
                    "text": hit_key,
                    "mapped_en": entries[hit_key],
                    "note": "中文提示词(需英文索引二次校验)",
                }
            )
            i += len(hit_key)
            continue
        hit_skip = next((s for s in skips if text.startswith(s, i)), None)
        if hit_skip:
            flush()
            skipped.append(hit_skip)
            spans.append({"status": "skip", "phrase": hit_skip, "text": hit_skip})
            i += len(hit_skip)
            continue
        if ch in zhmap.SKIP_CHARS:
            flush()
            skipped.append(ch)
            i += 1
            continue
        pending.append(ch)
        i += 1
    flush()
    return spans, skipped


# ---------------------------------------------------------------- 主流程
def analyze(
    text: str,
    lex: Lexicon,
    top: int = 3,
    compose_options: ComposeOptions | None = None,
) -> dict:
    """把输入切成片段、给每个片段选词, 组配后拼出检索式。"""
    text = (text or "").strip()
    if not text:
        raise ValueError("输入为空: 请给一个医学概念或一句临床问题")

    entries = zhmap.load()
    chunks: list[Chunk] = []
    skipped: list[str] = []

    for seg in _segments(text):
        if seg["lang"] == "en":
            spans, seg_skipped = _split_en(seg["tokens"], lex)
            skipped.extend(seg_skipped)
            for span in spans:
                if span["status"] == "skip":
                    continue
                display = seg["text"][span["rel_start"] : span["rel_end"]]
                chunks.append(
                    Chunk(
                        text=display,
                        lang="en",
                        status=span["status"],
                        phrase=span["phrase"],
                        note=span.get("note", ""),
                    )
                )
        else:
            spans, seg_skipped = _split_zh(seg["text"], lex, entries)
            skipped.extend(seg_skipped)
            for span in spans:
                if span["status"] == "skip":
                    continue
                chunks.append(
                    Chunk(
                        text=span.get("text", span["phrase"]),
                        lang="zh",
                        status=span["status"],
                        phrase=span["phrase"],
                        mapped_en=span.get("mapped_en", ""),
                        note=span.get("note", ""),
                    )
                )

    if not chunks:
        raise ValueError("没有识别出任何可检索片段, 请检查输入")

    # ---- 选词 ----
    for chunk in chunks:
        query_phrase = chunk.mapped_en or chunk.phrase
        cands = lex.candidates(query_phrase, limit=top) if query_phrase else []
        for cand in cands:
            cand["detail"] = candidate_detail(lex, cand)
        chunk.candidates = cands
        if cands:
            chunk.chosen = cands[0]
            if chunk.chosen["status"] != "确定":
                chunk.status = "unmatched"
                chunk.note = f"候选词状态为【待核】, 不写入 MeSH 字段"
            elif chunk.status != "matched":
                chunk.status = "matched"
        else:
            chunk.status = "unmatched"
            chunk.note = "本地词表未命中, 仅以自由词参与"

    skipped = _dedupe(skipped)
    options = compose_options or ComposeOptions()
    comp_results, comp_meta = compose_layer.apply(lex, chunks, options, text)
    query = build_query(chunks, comp_results, options)
    input_type = "单个医学概念" if len(chunks) == 1 else f"一句临床问题(切出 {len(chunks)} 个概念)"

    result = {
        "id": uuid.uuid4().hex[:12],
        "ts": datetime.now().astimezone().isoformat(timespec="seconds"),
        "input": text,
        "input_type": input_type,
        "mesh_year": lex.meta.get("mesh_year", str(paths.MESH_YEAR)),
        "index": {
            "descriptor_count": lex.meta.get("descriptor_count"),
            "term_count": lex.meta.get("term_count"),
            "built_at": lex.meta.get("built_at"),
        },
        "skipped": skipped,
        "chunks": [
            {
                "text": c.text,
                "lang": c.lang,
                "status": c.status,
                "phrase": c.phrase,
                "mapped_en": c.mapped_en,
                "note": c.note,
                "candidates": c.candidates,
                "chosen": c.chosen,
            }
            for c in chunks
        ],
        "query": query,
        "compose": {
            "options": options.to_dict(),
            "tag": mesh_tag(options),
            "intents": comp_meta["intents"],
            "per_chunk": comp_results,
        },
    }
    return result


def _dedupe(items: list[str]) -> list[str]:
    seen: set[str] = set()
    out = []
    for item in items:
        key = item.lower()
        if key not in seen:
            seen.add(key)
            out.append(item)
    return out


# ---------------------------------------------------------------- 检索式
def _free_text(chunk: dict) -> str:
    """自然语言字段: 英文用原句词形; 中文有英文提示词时用提示词(PubMed 语料为英文)。"""
    if chunk["lang"] == "zh" and chunk["mapped_en"]:
        return chunk["mapped_en"]
    return chunk["text"]


def build_query(
    chunks: list[Chunk] | list[dict],
    comp_results: list[dict] | None = None,
    options: ComposeOptions | None = None,
) -> str:
    """拼装: 每个概念 = (组配后的 MeSH 词 OR 自由词) —— 自然语言与受控词双保留。"""
    options = options or ComposeOptions()
    comp_results = comp_results or []
    tag = mesh_tag(options)
    clauses = []
    for index, chunk in enumerate(chunks):
        data = chunk if isinstance(chunk, dict) else _as_dict(chunk)
        free = _free_text(data).replace('"', "'").strip()
        comp = comp_results[index] if index < len(comp_results) else {}

        # ---- MeSH 侧 ----
        mesh_names: list[str] = []
        chosen = data.get("chosen")
        if chosen and chosen["status"] == "确定":
            base = chosen["name"].replace('"', "'")
            ec_used = next((h for h in comp.get("ec", []) if h.get("applied")), None)
            if ec_used:
                # 词表 Entry Combination 建议的组配改写
                base = str(ec_used.get("out_desc_name") or base).replace('"', "'")
                out_qual = ec_used.get("out_qual_name")
                mesh_names.append(f"{base}/{out_qual}" if out_qual else base)
            else:
                applied = comp.get("applied", [])
                if applied:
                    for item in applied:
                        mesh_names.append(f"{base}/{item['name']}")
                else:
                    mesh_names.append(base)

        parts = [f'"{name}"{tag}' for name in mesh_names]

        # ---- 自由词侧 (原句词形 + 可选同义词) ----
        free_terms = [free] if free else []
        for term in comp.get("free_terms", []):
            term = str(term).replace('"', "'").strip()
            if term and term.lower() not in {t.lower() for t in free_terms}:
                free_terms.append(term)
        parts.extend(f'"{term}"[Title/Abstract]' for term in free_terms)

        if not parts:
            continue
        clauses.append("(" + " OR ".join(parts) + ")")

    if not clauses:
        return ""
    if len(clauses) == 1:
        return clauses[0]
    return "\n  AND ".join(clauses)


def _as_dict(chunk: Chunk) -> dict:
    return {
        "text": chunk.text,
        "lang": chunk.lang,
        "status": chunk.status,
        "phrase": chunk.phrase,
        "mapped_en": chunk.mapped_en,
        "chosen": chunk.chosen,
    }


# ---------------------------------------------------------------- 报告
def _count(value: str | None) -> str:
    try:
        return f"{int(value):,}"
    except (TypeError, ValueError):
        return str(value or "?")


def _clip(text: str, limit: int = 150) -> str:
    text = " ".join((text or "").split())
    return text if len(text) <= limit else text[: limit - 1] + "..."


def render(result: dict, lex: Lexicon, recorded: bool = True) -> str:
    width = 70
    lines: list[str] = []
    lines.append("=" * width)
    lines.append(f"输入  : {result['input']}")
    lines.append(f"类型  : {result['input_type']}")
    lines.append(
        "词表  : MeSH {y} | 描述词 {d} | 术语 {t} | 构建于 {b}".format(
            y=result["mesh_year"],
            d=_count(result["index"].get("descriptor_count")),
            t=_count(result["index"].get("term_count")),
            b=(result["index"].get("built_at") or "?")[:10],
        )
    )
    lines.append("-" * width)

    for idx, chunk in enumerate(result["chunks"], 1):
        head = f"[{idx}/{len(result['chunks'])}] 片段 \"{chunk['text']}\""
        lines.append(head)
        tag = "已匹配" if chunk["status"] == "matched" else "待核"
        lines.append(f"  状态  : {tag}")
        if chunk["mapped_en"]:
            lines.append(f"  中文  : 提示词 -> {chunk['mapped_en']}")
        if chunk["note"]:
            lines.append(f"  切分  : {chunk['note']}")

        if not chunk["candidates"]:
            lines.append("  候选  : 无 (本地词表未命中, 建议改用更规范的英文术语)")
            lines.append("")
            continue

        for rank, cand in enumerate(chunk["candidates"], 1):
            lines.append(
                f"  选词{rank}  {cand['name']} ({cand['ui']})  [{cand['status']}] {cand['kind_label']}"
            )
            lines.append(f"      依据: {cand['basis_label']} (词表原词: \"{cand['matched_term']}\")")
            if rank == 1:
                lines.extend(_detail(lex, cand))

        comp = comp_result(result, idx - 1)
        lines.extend(_compose_lines(comp))
        lines.append("")

    if result["skipped"]:
        lines.append("跳过结构词: " + ", ".join(result["skipped"][:20]))

    pending = [c["text"] for c in result["chunks"] if c["status"] != "matched"]
    if pending:
        lines.append("待核片段  : " + " | ".join(f"\"{t}\"" for t in pending))
    lines.append("=" * width)
    lines.append("组配概览: " + "; ".join(_compose_summary(result)))
    lines.append("-" * width)
    lines.append("检索式 (可直接粘贴 PubMed):")
    lines.append(result["query"])
    lines.append("")
    tag = result["compose"]["tag"]
    lines.append(f"字段标签: {tag} = MeSH 主题词; [Title/Abstract] = 自然语言自由词")
    opts = result["compose"]["options"]
    if opts["noexp"] or opts["major"]:
        extra = []
        if opts["noexp"]:
            extra.append(":noexp 不爆炸下位词")
        if opts["major"]:
            extra.append("[majr] 仅主要主题")
        lines.append("          " + "; ".join(extra))
    lines.append("          副主题词只允许来自词表真实可用集合, 不可用即跳过")
    if recorded:
        lines.append(f"映射记录已保存: {paths.MAPPING_RECORD.relative_to(paths.PROJECT_ROOT)}")
    else:
        lines.append("映射记录: 本次未保存 (--no-record)")
    return "\n".join(lines)


# ---------------------------------------------------------------- 组配展示
def comp_result(result: dict, index: int) -> dict:
    items = (result.get("compose") or {}).get("per_chunk") or []
    return items[index] if 0 <= index < len(items) else {}


def _compose_lines(comp: dict) -> list[str]:
    out: list[str] = []
    if comp.get("note"):
        out.append(f"      组配  : {comp['note']}")
        return out
    for item in comp.get("applied", []):
        out.append(f"      组配  : /{item['name']} ({item['abbr']})  [来源: {item['source']}]")
    for item in comp.get("skipped", []):
        name = item.get("name") or item["spec"]
        out.append(f"      跳过  : /{name} - {item['reason']}")
    for hint in comp.get("ec", []):
        target = str(hint.get("out_desc_name") or "?")
        if hint.get("out_qual_name"):
            target += "/" + hint["out_qual_name"]
        state = "已采用" if hint.get("applied") else "仅提示, 加 --ec 采用"
        src = f"\"{hint['in_desc_name']}/{hint['in_qual']}\""
        out.append(f"      组配提示: {src} 词表建议改用 \"{target}\" ({state})")
    if comp.get("free_terms"):
        terms = ", ".join(comp["free_terms"])
        out.append(f"      自由词: +{len(comp['free_terms'])} 个同义词 ({terms})")
    return out


def _compose_summary(result: dict) -> list[str]:
    compose = result.get("compose") or {}
    opts = compose.get("options") or {}
    out = [f"字段={compose.get('tag')}"]
    intents = compose.get("intents") or []
    if intents:
        out.append("意图词=" + ", ".join(i["keyword"] for i in intents))
    out.append(f"自由词同义词={opts.get('free_syn', 0)}")
    out.append("Entry Combination=" + ("采用" if opts.get("ec") else "仅提示"))
    applied = sum(len(c.get("applied", [])) for c in compose.get("per_chunk", []))
    out.append(f"已配副主题词={applied} 个")
    return out


def _detail(lex: Lexicon, cand: dict) -> list[str]:
    """把 candidate_detail() 的结构化信息渲染成 CLI 的明细行。"""
    detail = cand.get("detail") or {}
    trees = detail.get("tree_numbers") or []
    out = []
    out.append("      树号  : " + ("; ".join(trees) if trees else "(无树号, 不支持上下位扩展)"))

    chain = detail.get("broader_chain") or []
    if chain:
        shown = " > ".join(f"{c['name']} ({c['tree_number']})" for c in chain[:3])
        more = f" ... 共 {len(chain)} 级" if len(chain) > 3 else ""
        out.append(f"      上位  : {shown}{more}  [全部去重 {detail.get('broader_total', 0)} 个]")
    elif trees:
        out.append("      上位  : 已到树根")

    if detail.get("narrower_total"):
        names = ", ".join(detail.get("narrower_examples") or [])
        out.append(f"      下位  : 共 {detail['narrower_total']} 个, 例: {names}")
    else:
        out.append("      下位  : 无 (已是最底层或不可细分)")

    quals = detail.get("qualifiers") or []
    if quals:
        shown = ", ".join(f"/{q['name']} ({q['abbr']})" for q in quals[:5])
        out.append(f"      副主题: 可用 {len(quals)} 个, 例: {shown}")
    else:
        out.append("      副主题: 无 (该词通常不配副主题词)")

    if detail.get("scope_note"):
        out.append(f"      作用域: {_clip(detail['scope_note'])}")
    return out


def candidate_detail(lex: Lexicon, cand: dict) -> dict:
    """候选词的层级/副主题词/作用域明细, 供 CLI 报告与网页共用 (纯结构化数据)。"""
    ui = cand["ui"]
    desc = lex.describe(ui) or {}
    broader = lex.broader(ui)
    narrower = lex.narrower(ui)
    return {
        "tree_numbers": lex.tree_numbers(ui),
        "broader_chain": [
            {"name": c["name"], "tree_number": c["tree_number"]} for c in broader["chain"]
        ],
        "broader_total": broader["total"],
        "narrower_total": narrower["total"],
        "narrower_examples": [n["name"] for n in narrower["items"]],
        "qualifiers": [
            {"name": q["name"], "abbr": q["abbr"]} for q in lex.qualifiers(ui)
        ],
        "scope_note": desc.get("scope_note") or "",
    }


# ---------------------------------------------------------------- 记录
def save_record(result: dict) -> None:
    paths.RECORDS_DIR.mkdir(parents=True, exist_ok=True)
    with open(paths.MAPPING_RECORD, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(result, ensure_ascii=False) + "\n")


def to_json(result: dict) -> str:
    return json.dumps(result, ensure_ascii=False, indent=2)
