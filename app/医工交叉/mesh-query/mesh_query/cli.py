"""命令行入口。

用法:
    python -m mesh_query "heart failure"
    python -m mesh_query "Does metformin reduce mortality in type 2 diabetes?"
    python -m mesh_query --top 5 "高血压 和 2型糖尿病"
    python -m mesh_query --json "pneumonia in elderly"
"""

from __future__ import annotations

import argparse
import sys

from . import __version__, paths, pipeline
from .console import enable_utf8_redirects
from .lexicon import Lexicon


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="mesh_query",
        description="把一个医学概念或一句临床问题映射成 PubMed 可用的 MeSH 检索式",
    )
    parser.add_argument("text", nargs="?", help="医学概念或一句临床问题")
    parser.add_argument("--top", type=int, default=3, help="每个片段显示的候选词数量 (默认 3)")
    parser.add_argument("--db", help="词表索引路径 (默认 data/mesh.db)")
    parser.add_argument("--json", action="store_true", help="输出 JSON 而非文本报告")
    parser.add_argument("--no-record", action="store_true", help="不写映射记录")
    # ---- 组配 ----
    parser.add_argument(
        "--subhead",
        help="手动指定副主题词: 'TU,AE' 全部概念; '1:AE,2:TU' 按概念序号; 可混用",
    )
    parser.add_argument(
        "--auto-subhead",
        action="store_true",
        help="按输入里的意图词(治疗/诊断/不良反应...)自动配副主题词",
    )
    parser.add_argument("--noexp", action="store_true", help="不爆炸下位词 ([MeSH Terms:noexp])")
    parser.add_argument("--major", action="store_true", help="仅主要主题 ([majr])")
    parser.add_argument("--free-syn", type=int, default=0, metavar="N", help="每个概念追加 N 个同义自由词")
    parser.add_argument("--ec", action="store_true", help="采用词表 Entry Combination 建议的组配改写")
    parser.add_argument("--serve", action="store_true", help="启动本地交互网页后退出")
    parser.add_argument("--port", type=int, default=8765, help="--serve 的端口 (默认 8765)")
    parser.add_argument("--version", action="version", version=f"mesh-query {__version__}")
    args = parser.parse_args(argv)

    # 重定向/管道时统一按 UTF-8 输出; 交互终端保持系统码页, 免得中文乱码
    enable_utf8_redirects()

    if args.serve:
        from . import web

        return web.serve(port=args.port, db=args.db, open_browser=True)

    if not args.text:
        parser.print_help()
        return 1

    try:
        options = _compose_options(args)
        with Lexicon(args.db) as lex:
            result = pipeline.analyze(args.text, lex, top=args.top, compose_options=options)
            if args.json:
                print(pipeline.to_json(result))
            else:
                print(pipeline.render(result, lex, recorded=not args.no_record))
            if not args.no_record:
                pipeline.save_record(result)
    except FileNotFoundError as exc:
        print(f"[mesh-query] {exc}", file=sys.stderr)
        return 2
    except ValueError as exc:
        print(f"[mesh-query] {exc}", file=sys.stderr)
        return 1
    return 0


def _compose_options(args: argparse.Namespace):
    from .compose import ComposeOptions, parse_subhead_arg

    if args.subhead:
        all_specs, by_index = parse_subhead_arg(args.subhead)
    else:
        all_specs, by_index = [], {}
    return ComposeOptions(
        subhead_all=all_specs,
        subhead_by_index=by_index,
        auto_subhead=args.auto_subhead,
        noexp=args.noexp,
        major=args.major,
        free_syn=max(0, args.free_syn),
        ec=args.ec,
    )


if __name__ == "__main__":
    raise SystemExit(main())
