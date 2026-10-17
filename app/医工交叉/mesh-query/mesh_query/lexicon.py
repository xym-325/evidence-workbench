"""本地词表查询层。

职责:
- 词形匹配: 首选词/入口词精确命中 -> 确定; 词集、模糊命中 -> 待核
- 层级查询: 由树号推导上位词链与下位词
- 副主题词: 描述词可用的副主题词及缩写

所有词都来自本地 data/mesh.db (由官方 MeSH XML 构建), 不发任何外部请求。
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

from . import paths
from .build_index import KIND_LABEL, normalize
from .stopwords import EN_STOP

# 术语种类排序权重 (越小越优先)
KIND_RANK = {
    "preferred": 0,
    "record_preferred": 1,
    "concept_preferred": 2,
    "concept": 3,
    "entry": 4,
    "permuted": 5,
}

# 匹配依据 -> 输出时的中文说明
BASIS_LABEL = {
    "exact": "与词表词形完全一致",
    "variant": "与词表词形一致(单复数词形变体)",
    "dropped_stop": "去掉结构词后与词表词形一致",
    "token_set": "词集完全一致但词序不同, 按同义词处理",
    "subset": "词表中存在包含输入全部词的更长词, 需人工确认",
    "loose": "按词片段模糊命中, 仅作线索, 需人工确认",
}


def _deplural(token: str) -> str:
    if len(token) > 3 and token.endswith("es") and token[-3] in "sxzh":
        return token[:-2]
    if len(token) > 3 and token.endswith("s") and not token.endswith("ss"):
        return token[:-1]
    return token


def _plural(token: str) -> str:
    if token.endswith(("s", "x", "z", "ch", "sh")):
        return token + "es"
    if len(token) > 1 and token.endswith("y") and token[-2] not in "aeiou":
        return token[:-1] + "ies"
    return token + "s"


def variants(norm: str) -> list[str]:
    """极轻量的单复数变体, 只做兜底, 不做词干还原。"""
    out = [norm]
    toks = norm.split()
    if not toks:
        return out
    singular = " ".join(_deplural(t) for t in toks)
    if singular != norm:
        out.append(singular)
    plural = " ".join(_plural(t) for t in toks)
    if plural != norm:
        out.append(plural)
    return out


def content_tokens(tokens: list[str]) -> list[str]:
    """剔除结构词后的词集 (两侧统一用这个口径比较)。"""
    out = []
    for tok in tokens:
        norm = normalize(tok)
        if norm and tok.lower() not in EN_STOP:
            out.append(norm)
    return out


class Lexicon:
    def __init__(self, db_path: Path | str | None = None):
        path = Path(db_path) if db_path else paths.DB_PATH
        if not path.exists():
            raise FileNotFoundError(
                f"词表索引不存在: {path}\n"
                "请先执行: python scripts/download_mesh.py && python -m mesh_query.build_index"
            )
        self.path = path
        self.conn = sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True)
        self.conn.row_factory = sqlite3.Row
        self._meta: dict[str, str] | None = None
        self._qual_index: dict[str, dict] | None = None

    def close(self) -> None:
        self.conn.close()

    def __enter__(self) -> "Lexicon":
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    # ---------------- 基本信息 ----------------
    @property
    def meta(self) -> dict[str, str]:
        if self._meta is None:
            self._meta = {r["k"]: r["v"] for r in self.conn.execute("SELECT k, v FROM meta")}
        return self._meta

    # ---------------- 词形匹配 ----------------
    def has_phrase(self, phrase: str) -> bool:
        """供切分用: 该词形(含单复数变体)是否存在于词表。"""
        norm = normalize(phrase)
        if not norm:
            return False
        for candidate in variants(norm):
            if self.conn.execute(
                "SELECT 1 FROM terms WHERE norm=? LIMIT 1", (candidate,)
            ).fetchone():
                return True
        return False

    def exact(self, phrase: str) -> tuple[list[sqlite3.Row], str]:
        """精确命中返回 (行列表, 实际命中的归一化词形)。"""
        norm = normalize(phrase)
        if not norm:
            return [], ""
        for candidate in variants(norm):
            rows = self.conn.execute(
                "SELECT term, kind, ui FROM terms WHERE norm=? "
                "ORDER BY CASE kind "
                " WHEN 'preferred' THEN 0 WHEN 'record_preferred' THEN 1 "
                " WHEN 'concept_preferred' THEN 2 WHEN 'concept' THEN 3 ELSE 4 END",
                (candidate,),
            ).fetchall()
            if rows:
                return list(rows), candidate
        return [], ""

    def _fts_rows(self, tokens: list[str], mode: str, fetch: int = 80) -> list[sqlite3.Row]:
        """FTS5 检索。tokens 需为归一化后的词列表。"""
        if not tokens:
            return []
        if mode == "loose":
            expr = " OR ".join(f'"{t}"*' for t in tokens)
        else:
            expr = " AND ".join(f'"{t}"' for t in tokens)
        try:
            rows = self.conn.execute(
                "SELECT term_norm, term, ui, kind, bm25(terms_fts) AS score "
                "FROM terms_fts WHERE terms_fts MATCH ? ORDER BY score LIMIT ?",
                (expr, fetch),
            ).fetchall()
        except sqlite3.OperationalError:
            return []

        wanted = set(tokens)
        kept = []
        for row in rows:
            token_set = set(row["term_norm"].split()) - EN_STOP
            if mode == "equal" and token_set != wanted:
                continue
            if mode == "subset" and not wanted <= token_set:
                continue
            kept.append(row)
        return kept

    def token_match(self, tokens: list[str]) -> sqlite3.Row | None:
        """切分用: 词集相等命中 (剔除结构词后两侧一致) 则返回命中行。"""
        wanted = content_tokens(tokens)
        if len(wanted) < 2:
            return None
        hits = self._fts_rows(wanted, "equal")
        return hits[0] if hits else None

    def candidates(self, phrase: str, limit: int = 5) -> list[dict]:
        """把一个片段映射成候选 MeSH 词, 每条自带匹配依据与状态。"""
        norm = normalize(phrase)
        if not norm:
            return []

        # 1) 原样精确
        rows, hit = self.exact(norm)
        if rows:
            return self._pack(rows, "exact" if hit == norm else "variant", limit)

        # 2) 去掉结构词后的精确 (如 "quality of life" -> "quality life" 不成立, 交给词集)
        raw_tokens = norm.split()
        wanted = content_tokens(raw_tokens)
        if wanted and wanted != raw_tokens:
            rows, _hit = self.exact(" ".join(wanted))
            if rows:
                return self._pack(rows, "dropped_stop", limit)

        # 3) 词集相等 / 词集超集
        if len(wanted) >= 2:
            for mode, basis in (("equal", "token_set"), ("subset", "subset")):
                hits = self._fts_rows(wanted, mode)
                if hits:
                    return self._pack(hits, basis, limit)

        # 4) 模糊线索
        if wanted:
            hits = self._fts_rows(wanted if len(wanted) >= 1 else raw_tokens, "loose")
            if hits:
                return self._pack(hits, "loose", limit)
        return []

    def _pack(self, rows: list[sqlite3.Row], basis: str, limit: int) -> list[dict]:
        """按描述词去重、排序, 并补齐描述词信息。"""
        status = "确定" if basis in ("exact", "variant", "dropped_stop", "token_set") else "待核"
        picked: list[tuple[str, str, str]] = []  # (ui, term, kind)
        seen: set[str] = set()
        for row in rows:
            ui = row["ui"]
            if ui in seen:
                continue
            seen.add(ui)
            picked.append((ui, row["term"], row["kind"]))
            if len(picked) >= max(limit, 1) * 4:
                break
        if not picked:
            return []

        placeholders = ",".join("?" for _ in picked)
        info = {
            r["ui"]: r
            for r in self.conn.execute(
                f"SELECT ui, name, dclass FROM descriptors WHERE ui IN ({placeholders})",
                [p[0] for p in picked],
            )
        }

        out = []
        for ui, term, kind in picked:
            desc = info.get(ui)
            if desc is None:
                continue
            out.append(
                {
                    "ui": ui,
                    "name": desc["name"],
                    "dclass": desc["dclass"],
                    "matched_term": term,
                    "kind": kind,
                    "kind_label": KIND_LABEL.get(kind, kind),
                    "basis": basis,
                    "basis_label": BASIS_LABEL.get(basis, basis),
                    "status": status,
                    "rank": KIND_RANK.get(kind, 9),
                }
            )
        out.sort(key=lambda c: c["rank"])
        return out[:limit]

    # ---------------- 层级: 树号 / 上位 / 下位 ----------------
    def tree_numbers(self, ui: str) -> list[str]:
        return [
            r["tree_number"]
            for r in self.conn.execute(
                "SELECT tree_number FROM tree_numbers WHERE ui=? ORDER BY tree_number", (ui,)
            )
        ]

    def broader(self, ui: str) -> dict:
        """上位词: 以第一条树号给出完整祖先链, 并统计各树去重后的祖先数。"""
        trees = self.tree_numbers(ui)
        if not trees:
            return {"chain": [], "total": 0, "trees": []}
        ancestors: set[str] = set()
        for tree in trees:
            parts = tree.split(".")
            for i in range(1, len(parts)):
                ancestors.add(".".join(parts[:i]))
        ancestors.difference_update(trees)

        chain = []
        parts = trees[0].split(".")
        for i in range(len(parts) - 1, 0, -1):
            prefix = ".".join(parts[:i])
            row = self.conn.execute(
                "SELECT d.ui, d.name FROM tree_numbers t "
                "JOIN descriptors d ON d.ui=t.ui WHERE t.tree_number=? LIMIT 1",
                (prefix,),
            ).fetchone()
            chain.append(
                {
                    "tree_number": prefix,
                    "ui": row["ui"] if row else None,
                    "name": row["name"] if row else "(树号未对应描述词)",
                }
            )
        return {"chain": chain, "total": len(ancestors), "trees": trees}

    def narrower(self, ui: str, limit: int = 5) -> dict:
        trees = self.tree_numbers(ui)
        if not trees:
            return {"items": [], "total": 0}
        seen: dict[str, str] = {}
        total = 0
        for tree in trees:
            prefix = tree + ".%"
            total += int(
                self.conn.execute(
                    "SELECT COUNT(DISTINCT t.ui) AS n FROM tree_numbers t "
                    "WHERE t.tree_number LIKE ?",
                    (prefix,),
                ).fetchone()["n"]
                or 0
            )
            for sub in self.conn.execute(
                "SELECT DISTINCT t.ui, d.name, t.tree_number FROM tree_numbers t "
                "JOIN descriptors d ON d.ui=t.ui WHERE t.tree_number LIKE ? "
                "ORDER BY t.tree_number",
                (prefix,),
            ).fetchall():
                seen.setdefault(sub["ui"], sub["name"])
        items = [{"ui": u, "name": n} for u, n in list(seen.items())[:limit]]
        return {"items": items, "total": total}

    # ---------------- 副主题词 / Entry Combination / 同义词 ----------------
    def qualifiers(self, ui: str) -> list[dict]:
        return [
            {"qual_ui": r["qual_ui"], "abbr": r["abbr"], "name": r["qual_name"]}
            for r in self.conn.execute(
                "SELECT qual_ui, abbr, qual_name FROM desc_quals WHERE ui=? ORDER BY abbr",
                (ui,),
            )
        ]

    @property
    def qual_index(self) -> dict[str, dict]:
        """副主题词的三重索引: 名称 / 缩写 / UI (小写) -> 词表条目。"""
        if self._qual_index is None:
            index: dict[str, dict] = {}
            for row in self.conn.execute("SELECT ui, name, abbr FROM quals"):
                item = {"ui": row["ui"], "name": row["name"], "abbr": row["abbr"]}
                index[row["name"].lower()] = item
                if row["abbr"]:
                    index[row["abbr"].lower()] = item
                index[row["ui"].lower()] = item
            self._qual_index = index
        return self._qual_index

    def resolve_qual(self, spec: str) -> dict | None:
        """按缩写或名称解析副主题词; 词表里没有就返回 None (绝不猜)。"""
        return self.qual_index.get((spec or "").strip().lower())

    def entry_combination(self, ui: str, qual_ui: str | None) -> list[dict]:
        """词表声明的组配改写: (ui + qual_ui) -> 推荐的 (描述词, 副主题词)。"""
        rows = self.conn.execute(
            "SELECT ui, ecin_qual, ecout_desc, ecout_qual FROM entry_combinations "
            "WHERE ui=? AND ecin_qual=?",
            (ui, qual_ui),
        ).fetchall()
        if not rows:
            return []
        out = []
        for row in rows:
            desc = self.describe(row["ecout_desc"]) or {}
            qual_name = None
            if row["ecout_qual"]:
                item = self.qual_index.get(row["ecout_qual"].lower())
                qual_name = item["name"] if item else row["ecout_qual"]
            out.append(
                {
                    "in_desc_ui": row["ui"],
                    "in_desc_name": (self.describe(row["ui"]) or {}).get("name", row["ui"]),
                    "in_qual_ui": row["ecin_qual"],
                    "out_desc_ui": row["ecout_desc"],
                    "out_desc_name": desc.get("name", row["ecout_desc"]),
                    "out_qual_ui": row["ecout_qual"],
                    "out_qual_name": qual_name,
                }
            )
        return out

    def entry_terms(self, ui: str, limit: int = 3) -> list[str]:
        """给自由词字段用的同义词: 入口词优先, 短词优先, 去掉与首选词重复的。"""
        desc = self.describe(ui) or {}
        preferred = (desc.get("name") or "").lower()
        rows = self.conn.execute(
            "SELECT term, kind FROM terms WHERE ui=? "
            "AND kind IN ('entry','permuted','record_preferred') "
            "ORDER BY CASE kind WHEN 'entry' THEN 0 WHEN 'record_preferred' THEN 1 ELSE 2 END, "
            "length(term), term",
            (ui,),
        ).fetchall()
        out: list[str] = []
        seen: set[str] = set()
        for row in rows:
            term = " ".join(row["term"].split())
            key = term.lower()
            if not term or key == preferred or key in seen:
                continue
            seen.add(key)
            out.append(term)
            if len(out) >= limit:
                break
        return out

    def describe(self, ui: str) -> dict | None:
        row = self.conn.execute(
            "SELECT ui, name, dclass, scope_note, annotation FROM descriptors WHERE ui=?",
            (ui,),
        ).fetchone()
        if row is None:
            return None
        note = row["scope_note"] or row["annotation"]
        return {
            "ui": row["ui"],
            "name": row["name"],
            "dclass": row["dclass"],
            "scope_note": " ".join(note.split()) if note else "",
        }
