"""组配层: 副主题词、Entry Combination、自由词扩展、爆炸与主要主题开关。

三条硬规则(保证不编造):
1. 副主题词只能从该描述词在词表里真实可用的副主题词集合中选, 不可用就跳过并说明;
2. 缩写/名称必须能在词表 quals 表里解析出来, 解析不到就跳过, 不猜;
3. Entry Combination 只来自词表 entry_combinations 表, 默认只提示, 加 --ec 才替换词形。
"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field


# ---------------------------------------------------------------- 选项
@dataclass
class ComposeOptions:
    subhead_all: list[str] = field(default_factory=list)   # 对所有概念生效的副主题词
    subhead_by_index: dict[int, list[str]] = field(default_factory=dict)  # 0-based 概念序号
    auto_subhead: bool = False      # 按输入里的意图词自动配副主题词
    noexp: bool = False             # [MeSH Terms:noexp] 不爆炸下位词
    major: bool = False             # [majr] 仅主要主题
    free_syn: int = 0               # 每个概念追加的自由词同义词个数
    ec: bool = False                # 采用词表的 Entry Combination 建议

    def to_dict(self) -> dict:
        data = asdict(self)
        data["subhead_by_index"] = {str(k): v for k, v in self.subhead_by_index.items()}
        return data


# ---------------------------------------------------------------- 意图 -> 副主题词
# 每条: (触发词, 官方副主题词名)。名称全部取自词表 quals 表的 76 个副主题词。
INTENT_ROUTES: tuple[tuple[tuple[str, ...], tuple[str, ...]], ...] = (
    (("treatment", "treat", "therapy", "therapeutic", "efficacy",
      "干预", "治疗", "疗效"), ("therapeutic use", "therapy")),
    (("pharmacotherapy", "medication", "drug therapy", "drug-treatment",
      "药物治疗", "用药"), ("drug therapy",)),
    (("adverse", "side effect", "side-effect", "safety", "toxicity",
      "不良反应", "副作用", "安全性", "毒性"), ("adverse effects",)),
    (("diagnosis", "diagnostic", "screening", "detection", "sensitivity",
      "specificity", "诊断", "筛查", "检测"), ("diagnosis",)),
    (("imaging", "magnetic resonance", "radiograph", "computed tomography",
      "影像", "磁共振", "断层扫描"), ("diagnostic imaging",)),
    (("prevention", "prevent", "prophylaxis", "预防"), ("prevention & control",)),
    (("mortality", "death", "fatal", "死亡", "死亡率"), ("mortality",)),
    (("etiology", "caused by", "risk factor", "predisposing",
      "病因", "危险因素"), ("etiology",)),
    (("epidemiology", "prevalence", "incidence",
      "流行病学", "患病率", "发病率"), ("epidemiology",)),
    (("surgery", "surgical", "operation", "perioperative",
      "手术", "外科", "围手术期"), ("surgery",)),
    (("rehabilitation", "康复", "复健"), ("rehabilitation",)),
    (("dosage", "dosing", "administration", "dose",
      "剂量", "用法", "给药"), ("administration & dosage",)),
    (("pharmacokinetic", "bioavailability", "药代", "生物利用度"), ("pharmacokinetics",)),
    (("pharmacology", "药理"), ("pharmacology",)),
    (("serum", "plasma", "hematolog", "血液", "血清", "血浆"), ("blood",)),
    (("physiolog", "生理"), ("physiology",)),
    (("metabolism", "metabolic", "代谢"), ("metabolism",)),
    (("genetic", "genomic", "基因", "遗传"), ("genetics",)),
    (("psycholog", "mental health", "心理", "精神"), ("psychology",)),
    (("complication", "comorbid", "并发症", "合并症"), ("complications",)),
    (("nursing", "nurse", "护理"), ("nursing",)),
    (("standard", "guideline", "quality assurance",
      "标准", "规范", "指南"), ("standards",)),
    (("method", "methodology", "方法", "方法学"), ("methods",)),
    (("patholog", "病理"), ("pathology",)),
    (("immunolog", "immune", "免疫"), ("immunology",)),
    (("microbiolog", "bacteri", "微生物", "细菌"), ("microbiology",)),
    (("diet", "nutrition", "饮食", "营养"), ("diet therapy",)),
    (("transplant", "移植"), ("transplantation",)),
)


def _is_cjk(text: str) -> bool:
    return any("一" <= ch <= "鿿" for ch in text)


def _keyword_hit(lower_text: str, keyword: str) -> bool:
    if _is_cjk(keyword):
        return keyword in lower_text
    pattern = r"(?<![a-z0-9])" + re.escape(keyword) + r"(?![a-z0-9])"
    return re.search(pattern, lower_text) is not None


def detect_intents(text: str) -> list[dict]:
    """从输入里找意图词; 每条路由最多触发一次, 用于说明"为什么配这个词"。"""
    lower = (text or "").lower()
    hits = []
    for keywords, qual_names in INTENT_ROUTES:
        for keyword in keywords:
            if _keyword_hit(lower, keyword):
                hits.append({"keyword": keyword, "qual_names": list(qual_names)})
                break
    return hits


def parse_subhead_arg(spec: str) -> tuple[list[str], dict[int, list[str]]]:
    """解析 --subhead: 'TU,AE' / '1:AE,2:TU' / 'AE,2:TU'(裸项对所有概念生效)。"""
    all_specs: list[str] = []
    by_index: dict[int, list[str]] = {}
    for raw in (spec or "").split(","):
        raw = raw.strip()
        if not raw:
            continue
        if ":" in raw:
            idx_text, _, names = raw.partition(":")
            index = int(idx_text.strip()) - 1      # 用户 1-based, 内部 0-based
            if index < 0:
                raise ValueError(f"--subhead 序号从 1 开始: {raw}")
            by_index.setdefault(index, []).extend(
                n.strip() for n in names.split("+") if n.strip()
            )
        else:
            all_specs.append(raw)
    return all_specs, by_index


# ---------------------------------------------------------------- 应用
def apply(lex, chunks: list, options: ComposeOptions, input_text: str) -> tuple[list[dict], dict]:
    """给每个概念做组配, 返回 (每个概念的组配结果, 全局信息)。

    自动路由按"路由"为单位: 一条意图可给多个候选副主题词(如 治疗 -> therapeutic use/therapy),
    取该描述词真实可用的第一个; 全都不可用才记为跳过并说明原因。
    """
    intents = detect_intents(input_text) if options.auto_subhead else []

    results: list[dict] = []
    for index, chunk in enumerate(chunks):
        entry: dict = {
            "index": index,
            "text": chunk.text,
            "requested": [],
            "applied": [],
            "skipped": [],
            "ec": [],
            "free_terms": [],
        }
        chosen = chunk.chosen
        if not chosen or chosen["status"] != "确定":
            entry["note"] = "该片段未确定 MeSH 词, 不做组配"
            results.append(entry)
            continue

        allowed = {q["qual_ui"]: q for q in lex.qualifiers(chosen["ui"])}
        used: set[str] = set()

        def record(resolved: dict, source: str, status: str, reason: str = "") -> None:
            item = {
                "spec": resolved["name"],
                "abbr": resolved["abbr"],
                "name": resolved["name"],
                "source": source,
            }
            entry["requested"].append(item)
            if status == "applied":
                entry["applied"].append({**item, "qual_ui": resolved["ui"]})
            else:
                entry["skipped"].append({**item, "reason": reason})

        def note_skip(spec: str, source: str, reason: str) -> None:
            entry["skipped"].append({"spec": spec, "source": source, "reason": reason})

        # ---- 1) 手动副主题词: 逐个按词表校验 ----
        manual: list[tuple[str, str]] = [(s, "手动") for s in options.subhead_all]
        manual.extend(
            (s, f"手动(第{index + 1}词)")
            for s in options.subhead_by_index.get(index, [])
        )
        for spec, source in manual:
            resolved = lex.resolve_qual(spec)
            if resolved is None:
                note_skip(spec, source, "词表中没有这个副主题词, 已跳过(不猜)")
                continue
            if resolved["ui"] in used:
                continue
            if resolved["ui"] not in allowed:
                record(resolved, source, "skip", "该词不支持此副主题词, 已跳过")
                continue
            used.add(resolved["ui"])
            record(resolved, source, "applied")

        # ---- 2) 自动意图路由: 每条意图取该词真实可用的第一个候选 ----
        for intent in intents:
            source = f"自动: {intent['keyword']}"
            fallback = None
            picked = None
            for name in intent["qual_names"]:
                resolved = lex.resolve_qual(name)
                if resolved is None:
                    continue                      # 词表里没有这个名字, 不猜
                if fallback is None:
                    fallback = resolved
                if resolved["ui"] in allowed:
                    picked = resolved
                    break
            if picked is None:
                if fallback is None:
                    note_skip(
                        intent["qual_names"][0], source,
                        "词表中没有这个副主题词, 已跳过(不猜)",
                    )
                elif fallback["ui"] not in used:
                    used.add(fallback["ui"])
                    record(fallback, source, "skip", "该词不支持此副主题词, 已跳过")
                continue
            if picked["ui"] in used:
                continue
            used.add(picked["ui"])
            record(picked, source, "applied")

        # Entry Combination 提示 / 采用
        for applied in entry["applied"]:
            for hint in lex.entry_combination(chosen["ui"], applied["qual_ui"]):
                hint = dict(hint)
                hint["in_qual"] = applied["name"]
                hint["applied"] = bool(options.ec)
                entry["ec"].append(hint)

        # 自由词同义词扩展
        if options.free_syn > 0:
            entry["free_terms"] = lex.entry_terms(chosen["ui"], options.free_syn)

        results.append(entry)

    return results, {"intents": intents}


def mesh_tag(options: ComposeOptions) -> str:
    """MeSH 字段标签; [majr] 与 :noexp 可组合为 [majr:noexp]。"""
    if options.major and options.noexp:
        return "[majr:noexp]"
    if options.major:
        return "[majr]"
    if options.noexp:
        return "[MeSH Terms:noexp]"
    return "[MeSH Terms]"
