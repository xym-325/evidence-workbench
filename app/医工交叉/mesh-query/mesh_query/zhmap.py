"""中文 -> 英文提示词表。

定位:
- 只提供"英文候选提示", 不是权威翻译; 最终以本地 MeSH 索引的二次校验为准。
- 映射后命不中词表、或只模糊命中的, 一律在输出里标【待核】。
- 数据在 data/zh_map.json, 直接加键值即可扩充; 结构词放到 skip 列表。

校验:
    python -m mesh_query.zhmap --verify
"""

from __future__ import annotations

import argparse
import json
import sys

from . import paths
from .console import enable_utf8_redirects

# 中文里的结构词/句法成分: 不单独检索, 切分时跳过
SKIP = [
    "患者", "病人", "人群", "研究", "探讨", "分析", "观察", "评估", "评价",
    "因素", "相关", "影响", "效果", "以及", "并且", "或者", "对于", "关于",
    "通过", "导致", "增加", "降低", "提高", "减少", "其", "等",
]

# 单字连接词: 出现在中文串里直接跳过
SKIP_CHARS = set("和与或的在对中等及被从为因所并且很更要这那些种类型了是有并")

# 中文段里的标点/分隔符: 切分时直接跳过
PUNCT = "，。、；：？！（）《》「」『』【】“”‘’…—·,. ;:()[]{}!?-–—"


def load(path=None) -> dict:
    path = path or paths.ZH_MAP_PATH
    if not path.exists():
        return {}
    with open(path, "r", encoding="utf-8") as fh:
        data = json.load(fh)
    entries = data.get("entries", data)
    return {str(k): str(v) for k, v in entries.items() if not str(k).startswith("_")}


def verify(lexicon=None, verbose: bool = True) -> list[dict]:
    """逐条用本地索引校验提示词, 返回 [{zh, en, status, hit}]。"""
    from .lexicon import Lexicon

    own = lexicon is None
    if own:
        lexicon = Lexicon()
    results = []
    try:
        for zh, en in sorted(load().items()):
            cands = lexicon.candidates(en, limit=3)
            hit = next((c for c in cands if c["status"] == "确定"), None)
            results.append(
                {
                    "zh": zh,
                    "en": en,
                    "status": "通过" if hit else "待核",
                    "hit": hit["name"] if hit else "",
                    "ui": hit["ui"] if hit else "",
                }
            )
    finally:
        if own:
            lexicon.close()

    if verbose:
        bad = [r for r in results if r["status"] == "待核"]
        print(f"中文提示表校验: 共 {len(results)} 条, 通过 {len(results) - len(bad)}, 待核 {len(bad)}")
        for r in bad:
            print(f"  [待核] {r['zh']} -> '{r['en']}' 未在词表中精确命中")
    return results


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="校验中文提示表")
    parser.add_argument("--verify", action="store_true", help="用本地词表逐条校验")
    args = parser.parse_args(argv)
    enable_utf8_redirects()
    if not args.verify:
        parser.print_help()
        return 1
    try:
        verify(verbose=True)
    except FileNotFoundError as exc:
        print(f"[zhmap] {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
