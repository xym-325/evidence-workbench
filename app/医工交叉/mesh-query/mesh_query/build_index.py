"""把 NLM 官方 MeSH XML 解析成本地 SQLite 词表索引。

数据源:
    data/raw/desc2026.gz   描述词 (Descriptors), 官方 gzip 压缩的 XML
    data/raw/qual2026.xml  副主题词 (Qualifiers)

产出:
    data/mesh.db
        descriptors   ui, 首选词, 归一化词形, DescriptorClass, 作用域注释
        tree_numbers  ui <-> 树号 (上下位关系靠树号前缀推导)
        terms         全部词形 (首选词/入口词/概念词/倒排词) + 归一化
        terms_fts     上表的 FTS5 索引, 用于词集/模糊匹配
        quals         副主题词全表
        desc_quals    描述词可用的副主题词及缩写
        meta          版本、构建时间、来源与 sha256

用法:
    python -m mesh_query.build_index
    python -m mesh_query.build_index --verify-map   # 顺带校验中文提示表
"""

from __future__ import annotations

import argparse
import gzip
import hashlib
import re
import sqlite3
import sys
import time
import unicodedata
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from pathlib import Path

from . import paths
from .console import enable_utf8_redirects

SCHEMA = """
CREATE TABLE descriptors (
    ui         TEXT PRIMARY KEY,
    name       TEXT NOT NULL,
    norm       TEXT NOT NULL,
    dclass     INTEGER,
    scope_note TEXT,
    annotation TEXT
);
CREATE INDEX idx_desc_norm ON descriptors(norm);

CREATE TABLE tree_numbers (
    ui          TEXT NOT NULL,
    tree_number TEXT NOT NULL,
    PRIMARY KEY (ui, tree_number)
);
CREATE INDEX idx_tree_number ON tree_numbers(tree_number);

CREATE TABLE terms (
    norm TEXT NOT NULL,
    term TEXT NOT NULL,
    ui   TEXT NOT NULL,
    kind TEXT NOT NULL,
    PRIMARY KEY (norm, ui, kind, term)
);
CREATE INDEX idx_terms_norm ON terms(norm);

CREATE TABLE quals (
    ui   TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    abbr TEXT,
    norm TEXT
);

CREATE TABLE desc_quals (
    ui        TEXT NOT NULL,
    qual_ui   TEXT NOT NULL,
    abbr      TEXT,
    qual_name TEXT,
    PRIMARY KEY (ui, qual_ui)
);

-- Entry Combination: 词表声明"某词/某副主题词"应改用另一组配
CREATE TABLE entry_combinations (
    ui         TEXT NOT NULL,  -- ECIN 描述词
    ecin_qual  TEXT,           -- ECIN 副主题词 (可空)
    ecout_desc TEXT NOT NULL,  -- ECOUT 描述词
    ecout_qual TEXT,           -- ECOUT 副主题词 (可空)
    PRIMARY KEY (ui, ecin_qual, ecout_desc, ecout_qual)
);
CREATE INDEX idx_ec_ui ON entry_combinations(ui);

CREATE TABLE meta (
    k TEXT PRIMARY KEY,
    v TEXT
);
"""

# 术语种类 -> 输出时的中文说法与排序权重 (越小越优先)
KIND_LABEL = {
    "preferred": "首选词",
    "record_preferred": "记录首选词",
    "concept": "概念词",
    "concept_preferred": "概念首选词",
    "entry": "入口词(同义词)",
    "permuted": "倒排/异形词",
}


def normalize(text: str) -> str:
    """归一化: NFKC + 小写 + 非字母数字折成空格。

    保留任意文字(含中文/希腊字母)的字母与数字, 便于中英两侧共用同一套规则。
    """
    text = unicodedata.normalize("NFKC", text or "").lower()
    return re.sub(r"[\W_]+", " ", text, flags=re.UNICODE).strip()


def sha256(path: Path, chunk: int = 1 << 20) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        while True:
            block = fh.read(chunk)
            if not block:
                break
            h.update(block)
    return h.hexdigest()


def _text(elem: ET.Element | None, path: str) -> str | None:
    if elem is None:
        return None
    value = elem.findtext(path)
    if value is None:
        return None
    value = value.strip()
    return value or None


def iter_descriptors(gz_path: Path):
    """流式产出 DescriptorRecord 元素, 内存占用恒定。"""
    with gzip.open(gz_path, "rb") as fh:
        for _event, elem in ET.iterparse(fh, events=("end",)):
            if elem.tag == "DescriptorRecord":
                yield elem
                elem.clear()  # 释放子树, 避免 313MB XML 撑爆内存


def iter_qualifiers(xml_path: Path):
    for _event, elem in ET.iterparse(str(xml_path), events=("end",)):
        if elem.tag != "QualifierRecord":
            continue
        ui = _text(elem, "QualifierUI")
        name = _text(elem, "QualifierName/String")
        if ui and name:
            yield ui, name
        elem.clear()


def build(
    db_path: Path,
    desc_gz: Path,
    qual_xml: Path,
    quiet: bool = False,
) -> dict:
    if not desc_gz.exists():
        raise FileNotFoundError(f"缺少 {desc_gz}, 请先运行 python scripts/download_mesh.py")
    if not qual_xml.exists():
        raise FileNotFoundError(f"缺少 {qual_xml}, 请先运行 python scripts/download_mesh.py")

    started = time.time()
    db_path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = db_path.with_suffix(".building.db")
    if tmp_path.exists():
        tmp_path.unlink()

    conn = sqlite3.connect(tmp_path)
    conn.execute("PRAGMA journal_mode=OFF")   # 一次性写入, 换取速度
    conn.execute("PRAGMA synchronous=OFF")
    conn.executescript(SCHEMA)
    if not _fts_available(conn):
        conn.close()
        raise RuntimeError("当前 SQLite 未启用 FTS5, 无法构建候选词匹配索引")
    conn.executescript(
        "CREATE VIRTUAL TABLE terms_fts USING fts5("
        "term_norm, term, ui UNINDEXED, kind UNINDEXED, "
        "tokenize='unicode61 remove_diacritics 2');"
    )

    stats = dict(descriptors=0, terms=0, tree_numbers=0, desc_quals=0, entry_combinations=0)
    qual_info: dict[str, tuple[str, str | None]] = {}
    rows_terms: list[tuple] = []

    def flush_terms() -> None:
        if not rows_terms:
            return
        conn.executemany("INSERT OR IGNORE INTO terms VALUES (?,?,?,?)", rows_terms)
        conn.executemany(
            "INSERT OR IGNORE INTO terms_fts(term_norm, term, ui, kind) VALUES (?,?,?,?)",
            [(r[0], r[1], r[2], r[3]) for r in rows_terms],
        )
        stats["terms"] += len(rows_terms)
        rows_terms.clear()

    with conn:
        # ---- 副主题词全表（名字先入, 缩写在描述词里补齐）----
        for qu, name in iter_qualifiers(qual_xml):
            qual_info.setdefault(qu, (name, None))
        conn.executemany(
            "INSERT OR IGNORE INTO quals(ui, name, norm) VALUES (?,?,?)",
            [(qu, name, normalize(name)) for qu, (name, _abbr) in qual_info.items()],
        )

        for rec in iter_descriptors(desc_gz):
            ui = _text(rec, "DescriptorUI")
            name = _text(rec, "DescriptorName/String")
            if not ui or not name:
                continue
            dclass = rec.get("DescriptorClass")

            # 作用域注释: 优先与首选词同名的概念, 否则第一个概念, 再退到 Annotation
            concepts = rec.findall("./ConceptList/Concept")
            scope = None
            for concept in concepts:
                if _text(concept, "ConceptName/String") == name:
                    scope = _text(concept, "ScopeNote")
                    break
            if scope is None and concepts:
                scope = _text(concepts[0], "ScopeNote")
            annotation = _text(rec, "Annotation")

            tree_nodes = rec.findall("./TreeNumberList/TreeNumber")
            tree_values = [t for t in ((n.text or "").strip() for n in tree_nodes) if t]

            conn.execute(
                "INSERT OR REPLACE INTO descriptors(ui, name, norm, dclass, scope_note, annotation) "
                "VALUES (?,?,?,?,?,?)",
                (ui, name, normalize(name), int(dclass) if dclass else None, scope, annotation),
            )
            stats["descriptors"] += 1

            if tree_values:
                conn.executemany(
                    "INSERT OR IGNORE INTO tree_numbers VALUES (?,?)",
                    [(ui, tv) for tv in tree_values],
                )
                stats["tree_numbers"] += len(tree_values)

            # ---- 词形 ----
            rows_terms.append((normalize(name), name, ui, "preferred"))
            for concept in concepts:
                cname = _text(concept, "ConceptName/String")
                if cname:
                    rows_terms.append((normalize(cname), cname, ui, "concept"))
                for term in concept.findall("./TermList/Term"):
                    text = _text(term, "String")
                    if not text:
                        continue
                    if term.get("RecordPreferredTermYN") == "Y":
                        kind = "record_preferred"
                    elif term.get("IsPermutedTermYN") == "Y":
                        kind = "permuted"
                    elif term.get("ConceptPreferredTermYN") == "Y":
                        kind = "concept_preferred"
                    else:
                        kind = "entry"
                    rows_terms.append((normalize(text), text, ui, kind))
            if len(rows_terms) >= 5000:
                flush_terms()

            # ---- 可用副主题词 ----
            pairs = []
            for aq in rec.findall("./AllowableQualifiersList/AllowableQualifier"):
                qu = _text(aq, "QualifierReferredTo/QualifierUI")
                qname = _text(aq, "QualifierReferredTo/QualifierName/String")
                abbr = _text(aq, "Abbreviation")
                if qu:
                    pairs.append((ui, qu, abbr, qname))
                    if qu in qual_info and abbr:
                        old_name, _old_abbr = qual_info[qu]
                        qual_info[qu] = (old_name, abbr)
            if pairs:
                conn.executemany(
                    "INSERT OR IGNORE INTO desc_quals VALUES (?,?,?,?)", pairs
                )
                stats["desc_quals"] += len(pairs)

            # ---- Entry Combination ----
            ecs = []
            for ec in rec.findall("./EntryCombinationList/EntryCombination"):
                ecin_desc = _text(ec, "ECIN/DescriptorReferredTo/DescriptorUI")
                ecin_qual = _text(ec, "ECIN/QualifierReferredTo/QualifierUI")
                ecout_desc = _text(ec, "ECOUT/DescriptorReferredTo/DescriptorUI")
                ecout_qual = _text(ec, "ECOUT/QualifierReferredTo/QualifierUI")
                if ecin_desc and ecout_desc:
                    ecs.append((ecin_desc, ecin_qual, ecout_desc, ecout_qual))
            if ecs:
                conn.executemany(
                    "INSERT OR IGNORE INTO entry_combinations VALUES (?,?,?,?)", ecs
                )
                stats["entry_combinations"] += len(ecs)

        flush_terms()

        # 用描述词里出现的缩写回填副主题词全表
        for qu, (name, abbr) in qual_info.items():
            if abbr:
                conn.execute("UPDATE quals SET abbr=? WHERE ui=?", (abbr, qu))

        # ---- FTS5 词表索引已在解析前创建 ----

        meta = {
            "mesh_year": str(paths.MESH_YEAR),
            "built_at": datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds"),
            "source_desc": str(desc_gz),
            "source_desc_sha256": sha256(desc_gz),
            "source_qual": str(qual_xml),
            "source_qual_sha256": sha256(qual_xml),
            "descriptor_count": str(stats["descriptors"]),
            "term_count": str(stats["terms"]),
            "tree_number_count": str(stats["tree_numbers"]),
            "desc_qual_count": str(stats["desc_quals"]),
            "entry_combination_count": str(stats["entry_combinations"]),
            "qual_count": str(len(qual_info)),
            "build_seconds": f"{time.time() - started:.1f}",
        }
        conn.executemany("INSERT OR REPLACE INTO meta VALUES (?,?)", meta.items())
        conn.execute("ANALYZE")
        conn.commit()

    conn.close()
    tmp_path.replace(db_path)

    if not quiet:
        print(f"[build_index] 写入 {db_path}")
        for key, value in stats.items():
            print(f"  {key:>16}: {value}")
        print(f"  {'elapsed':>16}: {meta['build_seconds']}s")
    return meta


def _fts_available(conn: sqlite3.Connection) -> bool:
    try:
        conn.execute("CREATE VIRTUAL TABLE IF NOT EXISTS _fts_probe USING fts5(x)")
        conn.execute("DROP TABLE _fts_probe")
        return True
    except sqlite3.OperationalError:
        return False


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="构建本地 MeSH 词表索引")
    parser.add_argument("--db", type=Path, default=paths.DB_PATH, help="输出 SQLite 路径")
    parser.add_argument("--desc", type=Path, default=paths.DESC_GZ, help="描述词 gzip XML")
    parser.add_argument("--qual", type=Path, default=paths.QUAL_XML, help="副主题词 XML")
    parser.add_argument("--verify-map", action="store_true", help="构建后校验中文提示表")
    args = parser.parse_args(argv)
    enable_utf8_redirects()

    try:
        build(args.db, args.desc, args.qual)
    except FileNotFoundError as exc:
        print(f"[build_index] {exc}", file=sys.stderr)
        return 2

    if args.verify_map:
        from .zhmap import verify

        verify(verbose=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
