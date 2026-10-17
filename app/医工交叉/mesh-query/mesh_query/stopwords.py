"""英文结构词/停用词。

用途有两处, 必须保持一致:
1. 切分: 这些词不当作独立检索概念 (如 "in patients with ...");
2. 词集匹配: 词集比较时两侧都剔除这些词
   (否则 "Quality of Life" 的词集 {quality, of, life} 对不上输入 {quality, life})。
"""

from __future__ import annotations

EN_STOP = {
    # 冠词/介词/连词
    "the", "a", "an", "in", "on", "at", "by", "for", "of", "to", "with", "and",
    "or", "nor", "vs", "versus", "among", "between", "from", "as", "into",
    "over", "under", "via", "upon", "per", "than", "then", "that", "those",
    "this", "these", "their", "our", "its", "her", "his",
    # 动词/系词
    "is", "are", "was", "were", "be", "been", "being", "do", "does", "did",
    "has", "have", "had", "can", "could", "will", "would", "should", "may",
    "might", "must", "shall",
    # 疑问/关系词
    "who", "whom", "whose", "which", "what", "when", "where", "why", "how",
    # 临床问题里的句法成分 (真正要检索的是后面的名词)
    "patients", "patient", "people", "subjects", "individuals", "cases",
    "compared", "compare", "using", "use", "used", "usage",
    "effect", "effects", "impact", "role", "association", "correlation",
    "among", "associated", "related", "increase", "decrease", "reduced",
    "risk", "outcome", "outcomes", "value", "level", "levels",
    # 问题句里的动词: 一般不作为检索概念
    "reduce", "reduces", "reducing", "increases", "increasing", "improve",
    "improves", "improving", "affect", "affects", "affecting", "influence",
    "influences", "treat", "treats", "treating", "prevention", "prevent",
    "prevents", "preventing", "cause", "causes", "caused", "leads", "lead",
    "relates", "relate", "shows", "show", "demonstrate", "demonstrates",
    "evaluate", "evaluates", "assess", "assesses", "determine", "determines",
    "investigate", "investigates", "examines", "explore",
}
