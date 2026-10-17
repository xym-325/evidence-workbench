# -*- coding: utf-8 -*-
"""学术智能体 CLI（v1 主入口）。

用法示例
--------
1) 一句自然语言直接跑通全流程（默认联网；失败自动标注并继续）：
     python -m src.cli --request "围绕低剂量CT肺结节检测的跨机型泛化，写一份面向医工交叉研究生的综述初稿"
2) 无网环境用本地样例跑通：
     python -m src.cli --request "..." --mock
3) 只生成任务卡：
     python -m src.cli task --request "..." -o configs/task.yaml
4) 用已有任务卡运行：
     python -m src.cli run --task configs/task.yaml
5) 单独核验引用：
     python -m src.cli verify --review outputs/<run>/review.md --refs outputs/<run>/references.json

流水线：parse_request -> plan_search -> fetch_literature -> screen_papers
        -> write_review -> verify_citations
运行产物全部落盘到 outputs/<run>/，保证可复查。
"""
import argparse
import datetime
import json
import os
import sys

from . import fetch_literature, miniyaml, parse_request, plan_search
from . import screen_papers, write_review, verify_citations


def _log(lines, msg):
    stamp = datetime.datetime.now().strftime("%H:%M:%S")
    line = "[%s] %s" % (stamp, msg)
    print(line)
    lines.append(line)


def _new_outdir(base, slug):
    stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
    path = os.path.join(base, "%s_%s" % (stamp, slug))
    os.makedirs(path, exist_ok=True)
    return path


def run_pipeline(task, outdir, log_lines):
    """执行完整流水线，落盘所有产物，返回结果字典。"""
    errors = parse_request.validate(task)
    if errors:
        for e in errors:
            _log(log_lines, "任务卡校验失败：" + e)
        return {"ok": False, "errors": errors}

    slug = parse_request.slugify(task.get("topic"))
    run_dir = outdir or _new_outdir(os.path.join(os.getcwd(), "outputs"), slug)
    os.makedirs(run_dir, exist_ok=True)
    _log(log_lines, "运行目录：" + run_dir)

    # 1) 任务卡落盘
    task_path = os.path.join(run_dir, "task.yaml")
    with open(task_path, "w", encoding="utf-8") as fh:
        fh.write(parse_request.to_yaml(task))
    _log(log_lines, "已写入任务卡 task.yaml")

    # 2) 检索计划
    plan = plan_search.plan(task)
    with open(os.path.join(run_dir, "retrieval_plan.txt"), "w", encoding="utf-8") as fh:
        fh.write(plan_search.plan_text(plan) + "\n")
    _log(log_lines, "检索计划：" + plan["query_zh"][:60])

    # 3) 检索
    records, papers = fetch_literature.fetch(task, outdir=run_dir)
    _log(log_lines, "检索结果：" + fetch_literature.summary_line(records))

    # 4) 筛选
    screened = screen_papers.screen(task, papers)
    stats = {"dedup": screened["dedup"], "included": len(screened["included"]),
             "excluded": len(screened["excluded"]), "adopted": len(screened["adopted"])}
    _log(log_lines, "筛选：去重 %d，纳入 %d，排除 %d，采用 %d"
         % (stats["dedup"], stats["included"], stats["excluded"], stats["adopted"]))
    if screened["note"]:
        _log(log_lines, "提示：" + screened["note"])

    # 纳入/排除表
    incl_rows = screen_papers.table_rows(screened["adopted"])
    excl_rows = screen_papers.table_rows(screened["excluded"], with_reason=True)
    with open(os.path.join(run_dir, "included.csv"), "w", encoding="utf-8-sig", newline="") as fh:
        fh.write("title,year,venue,source,identifier\n")
        fh.write(screen_papers.to_csv(incl_rows, ("title", "year", "venue", "source", "identifier")))
    with open(os.path.join(run_dir, "excluded.csv"), "w", encoding="utf-8-sig", newline="") as fh:
        fh.write(screen_papers.to_csv(excl_rows))
    _log(log_lines, "已写入 included.csv / excluded.csv")

    # 5) 综述（提纲 + 初稿）
    with open(os.path.join(run_dir, "outline.md"), "w", encoding="utf-8") as fh:
        fh.write(write_review.outline(task) + "\n")
    review_md, refs = write_review.write_review(task, screened["adopted"], records, stats)
    with open(os.path.join(run_dir, "review.md"), "w", encoding="utf-8") as fh:
        fh.write(review_md)
    with open(os.path.join(run_dir, "references.json"), "w", encoding="utf-8") as fh:
        json.dump(refs, fh, ensure_ascii=False, indent=2)
    _log(log_lines, "已写入 review.md / references.json（%d 条参考文献）" % len(refs))

    # 6) 核验
    report = verify_citations.verify(review_md, refs)
    with open(os.path.join(run_dir, "verification_report.md"), "w", encoding="utf-8") as fh:
        fh.write(verify_citations.report_text(report, "review.md", "references.json"))
    _log(log_lines, "引用核验：%s" % ("通过" if report["ok"] else "未通过"))

    # 7) 运行日志
    with open(os.path.join(run_dir, "run.log"), "w", encoding="utf-8") as fh:
        fh.write("\n".join(log_lines) + "\n")

    return {"ok": True, "run_dir": run_dir, "task": task, "plan": plan,
            "records": records, "stats": stats, "screened": screened,
            "review_md": review_md, "refs": refs, "report": report}


def _load_task_file(path):
    with open(path, "r", encoding="utf-8") as fh:
        text = fh.read()
    task = miniyaml.parse(text)
    return task


def _build_and_maybe_ask(args):
    task = parse_request.build_task(
        args.request, topic=args.topic, years=args.years, max_papers=args.max_papers,
        sources=(["mock"] if args.mock else None), audience=args.audience,
        review_words=args.review_words, mock=args.mock)
    if args.ask and not str(task.get("topic") or "").strip():
        print("提示：未从请求中识别出主题。")
        task = parse_request.prompt_missing(task)
    return task


def cmd_default(args):
    task = _build_and_maybe_ask(args)
    return run_pipeline(task, args.outdir, [])


def cmd_task(args):
    task = _build_and_maybe_ask(args)
    errors = parse_request.validate(task)
    if errors:
        print("任务卡校验失败：")
        for e in errors:
            print("  - " + e)
        return 1
    output = args.output or os.path.join("configs", "%s.yaml" % parse_request.slugify(task["topic"]))
    os.makedirs(os.path.dirname(output) or ".", exist_ok=True)
    with open(output, "w", encoding="utf-8") as fh:
        fh.write(parse_request.to_yaml(task))
    print("已生成任务卡：" + output)
    return 0


def cmd_run(args):
    task = _load_task_file(args.task)
    if not isinstance(task, dict):
        print("任务卡格式无法识别（应为「字段: 值」的 YAML）：%s" % args.task)
        return 1
    result = run_pipeline(task, args.outdir, [])
    return 0 if result.get("ok") else 1


def cmd_verify(args):
    with open(args.review, "r", encoding="utf-8") as fh:
        review_md = fh.read()
    with open(args.refs, "r", encoding="utf-8") as fh:
        refs = json.load(fh)
    report = verify_citations.verify(review_md, refs)
    text = verify_citations.report_text(report, args.review, args.refs)
    print(text)
    out = os.path.join(os.path.dirname(args.review), "verification_report.md")
    with open(out, "w", encoding="utf-8") as fh:
        fh.write(text)
    return 0 if report["ok"] else 2


def cmd_example(args):
    request = ("围绕低剂量CT肺结节检测的跨机型泛化问题，"
               "写一份面向医工交叉研究生的综述初稿；"
               "必须回答：物理域偏移与疾病谱偏移如何区分、"
               "单中心回顾验证为何高估性能、拒识与工作流如何协同。")
    task = parse_request.build_task(
        request,
        topic="低剂量CT肺结节检测的跨机型泛化",
        problem_tree_node="第三章-主分支2-跨场景降质",
        must_answer=["如何区分物理域偏移与疾病谱偏移？",
                     "为何单中心回顾验证会高估性能？",
                     "拒识与工作流交接为什么必须一起设计？"],
        mock=(args.mock or not args.net))
    print("示例请求：" + request)
    result = run_pipeline(task, args.outdir, [])
    return 0 if result.get("ok") else 1


def build_parser():
    p = argparse.ArgumentParser(
        prog="python -m src.cli",
        description="学术智能体 v1：自然语言 -> 任务卡 -> 检索 -> 筛选 -> 综述 -> 核验")
    p.add_argument("--request", "-r", help="自然语言研究要求（默认主流程）")
    p.add_argument("--topic", help="直接指定主题（覆盖从请求中提取的主题）")
    p.add_argument("--years", help="文献时间窗，如 2019-2026")
    p.add_argument("--max-papers", type=int, dest="max_papers", help="最大引用文献数")
    p.add_argument("--audience", help="读者对象")
    p.add_argument("--review-words", type=int, dest="review_words", help="正文字数目标")
    p.add_argument("--mock", action="store_true", help="只用本地 mock 数据源（离线可跑）")
    p.add_argument("--ask", action="store_true", help="缺失关键字段时交互式反问补全")
    p.add_argument("--outdir", help="产物输出目录（默认 outputs/<时间>_<主题>）")

    sub = p.add_subparsers(dest="cmd")

    sp_task = sub.add_parser("task", help="只生成任务卡 YAML")
    sp_task.add_argument("--request", "-r")
    sp_task.add_argument("--topic")
    sp_task.add_argument("--years")
    sp_task.add_argument("--max-papers", type=int, dest="max_papers")
    sp_task.add_argument("--audience")
    sp_task.add_argument("--review-words", type=int, dest="review_words")
    sp_task.add_argument("--mock", action="store_true")
    sp_task.add_argument("--ask", action="store_true")
    sp_task.add_argument("-o", "--output")
    sp_task.set_defaults(func=cmd_task)

    sp_run = sub.add_parser("run", help="用已有任务卡运行流水线")
    sp_run.add_argument("--task", required=True, help="任务卡 YAML 路径")
    sp_run.add_argument("--outdir")
    sp_run.set_defaults(func=cmd_run)

    sp_verify = sub.add_parser("verify", help="核验综述引用")
    sp_verify.add_argument("--review", required=True)
    sp_verify.add_argument("--refs", required=True)
    sp_verify.set_defaults(func=cmd_verify)

    sp_ex = sub.add_parser("example", help="跑内置示例任务")
    sp_ex.add_argument("--mock", action="store_true", default=True)
    sp_ex.add_argument("--net", action="store_true", help="示例也启用联网来源")
    sp_ex.add_argument("--outdir")
    sp_ex.set_defaults(func=cmd_example)
    return p


def main(argv=None):
    parser = build_parser()
    args = parser.parse_args(argv)
    if getattr(args, "cmd", None) is None and not args.request:
        parser.print_help()
        print("\n提示：先试试  python -m src.cli example --mock")
        return 0
    func = getattr(args, "func", cmd_default)
    result = func(args)
    if isinstance(result, int):
        return result
    if isinstance(result, dict):
        return 0 if result.get("ok") else 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
