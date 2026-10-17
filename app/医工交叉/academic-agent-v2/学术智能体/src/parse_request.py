# -*- coding: utf-8 -*-
"""parse_request：把一句自然语言的研究要求，变成结构化任务卡（task dict / YAML）。

设计原则（对应 AGENTS.md 硬约束）：
  - 不编造字段：从请求里能提取的提取，提取不到的用「明确标注的默认值」补齐；
  - 缺关键字段时，交互模式下必须「反问」补全（不会静默猜一个主题）；
  - 产出可读的 task.yaml，字段与 task.example.yaml 对齐。
"""
import re

# task 卡字段（与 task.example.yaml 对齐）
TOP_FIELDS = ("topic", "problem_tree_node", "audience", "years", "language",
              "sources", "max_papers", "inclusion", "exclusion", "must_answer", "output")
OUTPUT_FIELDS = ("review_words", "require_unresolved_section", "require_methods_declaration")
ALLOWED_SOURCES = ("openalex", "semantic_scholar", "mock")

# ---- 领域词表：用于在请求文本里识别主题关键词，并生成默认的纳入/必答问题 ----
DOMAIN_HINTS = {
    "肺结节": ("肺结节", "胸部CT", "低剂量", "肺癌", "lung nodule"),
    "影像": ("影像", "CT", "MRI", "X线", "超声", "病理", "imaging"),
    "脓毒症": ("脓毒症", "败血症", "sepsis", "急危重症", "预警"),
    "文本": ("病历", "文本", "NLP", "自然语言", "知识图谱", "报告"),
    "药物": ("药物", "分子", "成药", "drug", "化合物"),
    "信号": ("心电", "脑电", "ECG", "EEG", "信号", "波形"),
}

DEFAULT_INCLUSION = [
    "研究对象与综述主题直接相关",
    "报告了可复核的数据、方法或评价指标",
]
DEFAULT_EXCLUSION = [
    "与综述主题无关的其他学科文献",
    "仅新闻、评论或产品广告，无方法/数据说明",
]
DEFAULT_MUST_ANSWER = [
    "该主题目前的主流方法与代表性结论是什么？",
    "现有证据在数据、方法或评价上有哪些局限？",
    "要把结论落地还需要补齐哪些环节？",
]

TASK_TEMPLATE = {
    "problem_tree_node": "第二章-应用图谱-待定位",
    "audience": "医工交叉研究生",
    "years": "2019-2026",
    "language": "中文综述 + 中英检索",
    "sources": ["openalex", "semantic_scholar", "mock"],
    "max_papers": 25,
    "output": {
        "review_words": 4000,
        "require_unresolved_section": True,
        "require_methods_declaration": True,
    },
}


# --------------------------------------------------------------- 自然语言解析
def _extract_topic(request):
    """从自然语言里提炼一个主题短语（尽力而为，不做魔法）。"""
    text = (request or "").strip()
    if not text:
        return ""
    # 1)「围绕……的问题 / 关于……的综述 / 主题[是：:]……」优先
    m = re.search(r"(?:围绕|关于|针对|主题[是为：:]?)\s*[《\"“]?(.+?)[》\"”]?"
                  r"(?:的问题|的综述|的研究|,|，|。|\s*(?:写|做|生成))", text)
    if m:
        return m.group(1).strip("《》\"“” ")
    # 2) 冒号后内容
    if ":" in text or "：" in text:
        tail = re.split(r"[:：]", text, 1)[1].strip()
        if 4 <= len(tail) <= 60:
            return tail
    # 3) 退化：整句截断到 40 字
    return text[:40]


def _topic_problem_node(topic):
    for key, words in DOMAIN_HINTS.items():
        if any(w.lower() in topic.lower() for w in words):
            return "第二章-应用图谱-%s" % key
    return TASK_TEMPLATE["problem_tree_node"]


def build_task(request, *, topic=None, years=None, max_papers=None, sources=None,
               audience=None, problem_tree_node=None,
               must_answer=None, inclusion=None, exclusion=None,
               review_words=None, mock=False):
    """把自然语言 + 可选覆盖项，组装成完整任务卡（缺失字段用默认值）。"""
    topic = topic or _extract_topic(request)
    task = {
        "topic": topic,
        "problem_tree_node": problem_tree_node or _topic_problem_node(topic),
        "audience": audience or TASK_TEMPLATE["audience"],
        "years": years or TASK_TEMPLATE["years"],
        "language": TASK_TEMPLATE["language"],
        "sources": list(sources or (["mock"] if mock else TASK_TEMPLATE["sources"])),
        "max_papers": int(max_papers or TASK_TEMPLATE["max_papers"]),
        "inclusion": list(inclusion or DEFAULT_INCLUSION),
        "exclusion": list(exclusion or DEFAULT_EXCLUSION),
        "must_answer": list(must_answer or DEFAULT_MUST_ANSWER),
        "output": {
            "review_words": int(review_words or TASK_TEMPLATE["output"]["review_words"]),
            "require_unresolved_section": True,
            "require_methods_declaration": True,
        },
    }
    return task


# --------------------------------------------------------------- 校验
def _parse_years(value):
    s = str(value).strip()
    m = re.search(r"(\d{4})\s*[-–—~～至到]\s*(\d{4})", s)
    if m:
        lo, hi = int(m.group(1)), int(m.group(2))
        return (lo, hi) if lo <= hi else None
    if re.fullmatch(r"\d{4}", s):
        y = int(s)
        return (y, y)
    return None


def validate(task):
    """返回错误列表；空列表表示任务卡可用。"""
    errors = []
    if not isinstance(task, dict):
        return ["任务卡需要是「字段: 值」的映射结构"]
    for name in ("topic", "problem_tree_node", "audience", "language"):
        if not str(task.get(name) or "").strip():
            errors.append("缺少字段或为空：%s" % name)
    if _parse_years(task.get("years")) is None:
        errors.append("字段 years 格式应为 2019-2026 这样的年份区间，当前值：%s" % task.get("years"))
    srcs = task.get("sources")
    if not isinstance(srcs, list) or not srcs:
        errors.append("字段 sources 需为非空列表，例如 [openalex, mock]")
    else:
        bad = [s for s in srcs if str(s).lower() not in ALLOWED_SOURCES]
        if bad:
            errors.append("不支持的检索源：%s（仅支持 %s）" % ("、".join(bad), " / ".join(ALLOWED_SOURCES)))
    try:
        if not (1 <= int(task.get("max_papers")) <= 500):
            raise ValueError
    except (TypeError, ValueError):
        errors.append("字段 max_papers 需为 1-500 的整数，当前值：%s" % task.get("max_papers"))
    for name in ("inclusion", "exclusion", "must_answer"):
        items = task.get(name)
        if not isinstance(items, list) or not [i for i in items if str(i).strip()]:
            errors.append("字段 %s 需为非空列表" % name)
    out = task.get("output")
    if not isinstance(out, dict):
        errors.append("字段 output 需为嵌套字段")
    else:
        try:
            if not (1 <= int(out.get("review_words")) <= 20000):
                raise ValueError
        except (TypeError, ValueError):
            errors.append("字段 output.review_words 需为 1-20000 的整数")
    return errors


# --------------------------------------------------------------- 反问补全
def prompt_missing(task, ask=input, say=print):
    """交互式补全：对缺失的关键字段逐一反问（提示词 C 示例2 要求的「反问」）。"""
    if not str(task.get("topic") or "").strip():
        task["topic"] = ask("请用一句话说明综述主题：").strip()
        task["problem_tree_node"] = task.get("problem_tree_node") or _topic_problem_node(task["topic"])
    if not task.get("must_answer"):
        say("（可选）请输入 1-3 个必须回答的子问题，空行结束：")
        items = []
        while True:
            line = ask("  > ").strip()
            if not line:
                break
            items.append(line)
        if items:
            task["must_answer"] = items
    if not task.get("years"):
        line = ask("文献时间窗（如 2019-2026，直接回车用默认 %s）：" % TASK_TEMPLATE["years"]).strip()
        task["years"] = line or TASK_TEMPLATE["years"]
    return task


# --------------------------------------------------------------- YAML 输出
def _needs_quote(s):
    if s != s.strip() or s == "":
        return True
    if any(c in s for c in ":#{}[]&*!|>'\"%@`,"):
        return True
    if s.lower() in ("true", "false", "null", "yes", "no", "on", "off", "~"):
        return True
    if re.fullmatch(r"[-+]?\d+(\.\d+)?", s):
        return True
    return False


def _scalar(value):
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return str(value)
    s = str(value)
    if _needs_quote(s):
        return '"' + s.replace("\\", "\\\\").replace('"', '\\"') + '"'
    return s


def to_yaml(task):
    """把任务卡序列化成 YAML 文本（不依赖 PyYAML）。"""
    lines = ["# 自动生成的综述任务卡（task.yaml）", "# 生成器：parse_request.py"]
    simple = ("topic", "problem_tree_node", "audience", "years", "language")
    for name in simple:
        lines.append("%s: %s" % (name, _scalar(task.get(name, ""))))
    srcs = task.get("sources") or []
    lines.append("sources: [%s]" % ", ".join(str(s) for s in srcs))
    lines.append("max_papers: %s" % _scalar(task.get("max_papers", 25)))
    for name in ("inclusion", "exclusion", "must_answer"):
        lines.append("%s:" % name)
        for item in task.get(name) or []:
            lines.append("  - %s" % _scalar(item))
    out = task.get("output") or {}
    lines.append("output:")
    lines.append("  review_words: %s" % _scalar(out.get("review_words", 4000)))
    lines.append("  require_unresolved_section: %s"
                 % _scalar(bool(out.get("require_unresolved_section", True))))
    lines.append("  require_methods_declaration: %s"
                 % _scalar(bool(out.get("require_methods_declaration", True))))
    return "\n".join(lines) + "\n"


def slugify(text, fallback="review"):
    text = re.sub(r"[^\w\u4e00-\u9fff]+", "-", str(text or "")).strip("-")
    return (text[:40] or fallback)
