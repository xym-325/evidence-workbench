# -*- coding: utf-8 -*-
"""科研演示：任务卡、示例语料与独立核验样本。

mock 材料不得作为真实文献；独立核验样本不自动用于综述核验。
"""

# 默认 PICO / 临床问题（用于第 1 步生成 MeSH 检索式）
DEFAULT_PICO = (
    "Does deep learning improve precision classification and endotyping "
    "of knee osteoarthritis using imaging or multi-omics data?"
)

# 第 2 步：KOA 任务卡（与「学术智能体」的 task.example.yaml 同构）
TASK_YAML = """\
# KOA 精准分型任务卡（循证工作台演示）
topic: AI methods for precision subtyping endotyping of knee osteoarthritis
problem_tree_node: 医工交叉-主分支3-精准分型
audience: 骨科与风湿科科研人员、医学研究生
years: 2021-2026
language: 中文综述 + 中英检索
sources: [mock]
max_papers: 8
inclusion:
  - involves knee osteoarthritis classification or endotyping
  - uses deep learning or machine learning on imaging or omics data
exclusion:
  - backbone or leaderboard
  - natural language processing
must_answer:
  - 影像学路线与组学路线分别如何实现 KOA 精准分型？
  - 多模态/多组学深度学习分型的关键方法学是什么？
  - 现有研究在外部验证与可推广性上存在哪些不足？
output:
  review_words: 1200
  require_unresolved_section: true
  require_methods_declaration: true
"""

# KOA 语料：注入「学术智能体」的 mock 源（不修改原项目文件，运行时替换）
# 前 5 条符合纳入；后 3 条覆盖「命中排除 / 未满足纳入 / 年份越界」
KOA_MOCK = [
    {
        "title": "Deep learning-based clustering for endotyping and post-arthroplasty "
                 "response classification using knee osteoarthritis multiomic data",
        "authors": ["Rockel JS", "Kapoor M"],
        "year": 2025,
        "venue": "Annals of the Rheumatic Diseases",
        "abstract": "We developed a multimodal deep learning framework that clusters "
                    "multi-omics data (plasma, synovial fluid, urine microRNA and "
                    "metabolites) to identify distinct knee osteoarthritis endotypes and "
                    "classify one-year post-arthroplasty pain and function response. A "
                    "variational autoencoder with k-means yielded three patient clusters "
                    "and improved outcome classification, supporting precision medicine.",
        "source": "mock", "url": "", "doi": "10.1016/j.ard.2025.01.012",
    },
    {
        "title": "Deep learning grading of radiographic knee osteoarthritis severity: "
                 "a multi-center external validation study",
        "authors": ["Tiulpin A", "Saarakkala S"],
        "year": 2023,
        "venue": "Osteoarthritis and Cartilage",
        "abstract": "A convolutional neural network grades Kellgren-Lawrence severity of "
                    "knee osteoarthritis from plain radiographs. Multi-center external "
                    "validation and stratified analysis by sex and body mass index are "
                    "reported, showing that classification performance generalizes across "
                    "institutions better than single-center training.",
        "source": "mock", "url": "", "doi": "",
    },
    {
        "title": "MRI-based deep learning radiomics predicts knee osteoarthritis "
                 "progression: the MOST cohort",
        "authors": ["Wang Y", "Liu X"],
        "year": 2024,
        "venue": "Radiology",
        "abstract": "We build a deep learning radiomics model on MRI to predict incident "
                    "and progressive knee osteoarthritis. External validation on an "
                    "independent cohort demonstrates that multi-task learning improves "
                    "classification of progressors versus non-progressors, enabling "
                    "precision stratification of knee osteoarthritis patients.",
        "source": "mock", "url": "", "doi": "",
    },
    {
        "title": "Machine learning identifies molecular endotypes of knee osteoarthritis "
                 "from synovial fluid metabolomics",
        "authors": ["Dunn SL", "Anand S"],
        "year": 2022,
        "venue": "Osteoarthritis and Cartilage",
        "abstract": "Using metabolomics of synovial fluid, unsupervised machine learning "
                    "and consensus clustering identify molecular knee osteoarthritis "
                    "endotypes that differ in inflammatory and catabolic pathways, "
                    "supporting a precision medicine view in which classification drives "
                    "subtype-specific treatment.",
        "source": "mock", "url": "", "doi": "",
    },
    {
        "title": "Multimodal deep learning for pain phenotyping in knee osteoarthritis "
                 "using imaging and clinical data",
        "authors": ["Giordano R", "Arendt-Nielsen L"],
        "year": 2025,
        "venue": "European Journal of Pain",
        "abstract": "We integrate radiological and clinical variables in a multimodal "
                    "deep learning model to phenotype knee osteoarthritis pain and "
                    "predict treatment response. Endotyping improves prediction of "
                    "response to analgesics compared with a single-domain classifier, "
                    "with external testing reported.",
        "source": "mock", "url": "", "doi": "",
    },
    {
        "title": "Backbone tuning to climb the public leaderboard for knee osteoarthritis "
                 "KL grading",
        "authors": ["Zhao W"],
        "year": 2023,
        "venue": "MICCAI Workshop",
        "abstract": "We swap backbone networks and tune augmentation to improve knee "
                    "osteoarthritis KL grading on a public leaderboard. Results are "
                    "reported only on the public benchmark without external validation "
                    "and without stratified analysis.",
        "source": "mock", "url": "", "doi": "",
    },
    {
        "title": "A survey of natural language processing for clinical text "
                 "de-identification",
        "authors": ["O'Neil K"],
        "year": 2022,
        "venue": "Journal of Biomedical Informatics",
        "abstract": "This survey reviews natural language processing methods for clinical "
                    "text de-identification. No medical imaging or knee osteoarthritis "
                    "component is involved.",
        "source": "mock", "url": "", "doi": "",
    },
    {
        "title": "Deep learning triage of knee radiographs in the emergency department",
        "authors": ["Brown S"],
        "year": 2019,
        "venue": "Annals of Emergency Medicine",
        "abstract": "A convolutional classifier triages knee radiographs in the emergency "
                    "department to prioritize worklist ordering. This study predates the "
                    "review time window and does not address precision classification of "
                    "knee osteoarthritis.",
        "source": "mock", "url": "", "doi": "",
    },
]

# 第 3 步：待核验参考文献（2 真 1 假）
DEMO_REFS = [
    {
        "title": "Deep learning-based clustering for endotyping and post-arthroplasty "
                 "response classification using knee osteoarthritis multiomic data.",
        "author": "Rockel JS", "year": 2025, "doi": "10.1016/j.ard.2025.01.012",
    },
    {
        "title": "AI in health and medicine",
        "author": "Rajpurkar P", "year": 2022, "doi": "10.1038/s41591-021-01614-0",
    },
    {
        "title": "Hospital-invariant feature alignment for multi-center chest radiograph "
                 "triage",
        "author": "Zhang L", "year": 2024, "doi": "10.1016/j.media.2024.199999",
    },
]

# 断网时的离线核验缓存（仅用于演示，页面会标注「离线缓存」）


# ---------------------------------------------------------------- 示例一：肺结节
LUNG_PICO = (
    "Can deep learning models generalize across CT scanner manufacturers for "
    "low-dose CT lung nodule detection?"
)

LUNG_TASK = """\
topic: 低剂量CT肺结节检测的跨机型泛化
problem_tree_node: 第三章-主分支2-跨场景降质
audience: 医工交叉研究生
years: 2019-2026
language: 中文综述 + 中英检索
sources: [mock]
max_papers: 25
inclusion:
  - 涉及肺结节或胸部CT检测/诊断
  - 讨论域偏移、域泛化、域适应、外部验证或跨设备评价
exclusion:
  - 仅改骨干网络刷公开榜、无外部/分层评价
  - 与影像诊断无关的纯NLP综述
must_answer:
  - 已有研究采用了哪些方法改善CT肺结节检测的跨设备或跨数据集表现？
  - 这些方法在哪些数据与指标上得到验证，能否迁移到低剂量场景？
  - 研究之间有哪些一致结果、差异与尚待验证的条件？
output:
  review_words: 4000
  require_unresolved_section: true
  require_methods_declaration: true
"""

LUNG_REFS = [
    {"title": "AI in health and medicine", "author": "Rajpurkar P", "year": 2022,
     "doi": "10.1038/s41591-021-01614-0"},
    {"title": "Highly accurate protein structure prediction with AlphaFold",
     "author": "Jumper J", "year": 2021, "doi": "10.1038/s41586-021-03819-2"},
    {"title": "Contrastive pretraining for site-invariant pediatric chest radiograph "
              "triage", "author": "Chen W", "year": 2023,
     "doi": "10.1148/ryai.2023.999999"},
]

# ---------------------------------------------------------------- 通用跨领域语料（非医学示例用）
def _gp(title, authors, year, venue, abstract):
    return {"title": title, "authors": authors, "year": year, "venue": venue,
            "abstract": abstract, "source": "mock", "url": "", "doi": ""}


GENERIC_MOCK = [
    _gp("Short-video use and sustained attention in college students: a cross-sectional study",
        ["Li H", "Zhang Q"], 2023, "Computers in Human Behavior",
        "We examine the association between short-video use and sustained attention among "
        "college students. Heavy short-video use was associated with lower sustained attention "
        "scores, but causality cannot be established in this cross-sectional study. We recommend "
        "longitudinal designs and objective attention measures."),
    _gp("Spaced repetition and mobile micro-learning improve retention in higher education",
        ["Kumar S", "Wang Y"], 2022, "British Journal of Educational Technology",
        "In a randomized study we compare spaced repetition with massed practice in mobile "
        "micro-learning. Spaced repetition improved long-term retention and learning analytics "
        "showed better engagement; effect sizes were moderate."),
    _gp("Parameter-efficient fine-tuning of large language models: a comparative study",
        ["Chen L", "Patel R"], 2024, "NeurIPS",
        "We compare parameter-efficient fine-tuning methods (LoRA, adapters, prefix tuning) for "
        "large language models across tasks and scales. LoRA matched full fine-tuning at a "
        "fraction of the cost, while sensitivity to rank and learning rate remained."),
    _gp("Retrieval-augmented generation reduces hallucination in large language models",
        ["Ng H", "Okafor A"], 2023, "ACL",
        "We evaluate retrieval-augmented generation (RAG) for factual question answering. RAG "
        "substantially reduced hallucination compared with closed-book generation, but answer "
        "quality depended on retrieval recall and evidence grounding."),
    _gp("Remote work and productivity: evidence from a field experiment",
        ["Bloom N", "Liang J"], 2023, "Management Science",
        "A randomized field experiment with hybrid work arrangements measured productivity over "
        "two years. Fully remote work slightly reduced promotion rates while hybrid arrangements "
        "kept output stable; effects varied with team coordination."),
    _gp("Social media use and adolescent well-being: a longitudinal analysis",
        ["Orben A", "Przybylski A"], 2022, "Nature Human Behaviour",
        "Using longitudinal panel data we estimate the association between social media use and "
        "adolescent well-being. Effect sizes were small and sensitive to specification, "
        "suggesting caution about strong causal claims."),
    _gp("Coffee consumption and health outcomes: an umbrella review of meta-analyses",
        ["Poole R", "Kennedy O"], 2022, "BMJ",
        "An umbrella review of meta-analyses found that moderate coffee consumption was "
        "associated with lower risk of several conditions, while very high intake showed "
        "adverse associations. Most evidence is observational and cannot prove causality."),
    _gp("Urban green space and mental health: a systematic review",
        ["Dzhambov A", "Markevych I"], 2021, "Environmental Research",
        "A systematic review of observational studies reports consistent associations between "
        "residential green space and better mental health, but confounding and exposure "
        "measurement limit causal inference."),
    _gp("Minimum wage increases and employment: evidence from state-level policy changes",
        ["Cengiz D", "Dube A"], 2020, "American Economic Review",
        "Using state-level minimum wage changes we estimate employment effects with a "
        "border-discontinuity design. Results show little disemployment effect at moderate "
        "levels, with heterogeneity across regions."),
]

# ---------------------------------------------------------------- 非医学示例
EDU_PICO = "Does short-video use affect sustained attention and learning in college students?"
EDU_TASK = """\
topic: 短视频使用对大学生注意力的影响
problem_tree_node: 课程论文-教育心理
audience: 大学通识课学生
years: 2019-2026
language: 中文综述 + 中英检索
sources: [mock]
max_papers: 8
inclusion:
  - short-video or short-form video
  - college students or university students
  - attention
exclusion:
  - advertising or marketing
must_answer:
  - 现有研究认为短视频使用与注意力存在怎样的关系？
  - 这些研究在因果推断上有哪些局限？
  - 对学习者有哪些可操作的建议？
output:
  review_words: 1200
  require_unresolved_section: true
  require_methods_declaration: true
"""

TECH_PICO = "Which parameter-efficient fine-tuning methods work best for large language models?"
TECH_TASK = """\
topic: 大语言模型的高效微调方法对比
problem_tree_node: 技术调研-AI工程
audience: 计算机专业学生 / 工程师
years: 2019-2026
language: 中文综述 + 中英检索
sources: [mock]
max_papers: 8
inclusion:
  - language model or fine-tuning or retrieval
  - methods or study or evaluate
exclusion:
  - hardware or survey
must_answer:
  - 主流的大模型高效微调方法有哪些，效果如何？
  - 检索增强生成（RAG）如何降低幻觉？
  - 落地时如何权衡成本与效果？
output:
  review_words: 1200
  require_unresolved_section: true
  require_methods_declaration: true
"""

SOCIAL_PICO = "What is the effect of remote work on productivity?"
SOCIAL_TASK = """\
topic: 远程办公对生产率的影响
problem_tree_node: 通识-管理经济
audience: 职场读者 / 管理学生
years: 2019-2026
language: 中文综述 + 中英检索
sources: [mock]
max_papers: 8
inclusion:
  - remote work or employment or productivity
  - experiment or data or evidence
exclusion:
  - opinion or editorial
must_answer:
  - 远程 / 混合办公对生产率有什么证据？
  - 这些结论的适用边界在哪里？
  - 对企业管理有什么启示？
output:
  review_words: 1200
  require_unresolved_section: true
  require_methods_declaration: true
"""

EXAMPLES = [
    {"mode": "pro", "name": "医学 · 膝骨关节炎精准分型（KOA）",
     "desc": "影像 + 多组学深度学习分型；含 2 真 1 假的引用核验",
     "pico": DEFAULT_PICO, "task_yaml": TASK_YAML, "refs": DEMO_REFS},
    {"mode": "pro", "name": "医学 · 低剂量CT肺结节跨机型泛化",
     "desc": "域偏移 / 外部验证",
     "pico": LUNG_PICO, "task_yaml": LUNG_TASK, "refs": LUNG_REFS},
    {"mode": "both", "name": "教育 · 短视频与大学生注意力",
     "desc": "非医学示例：教育心理",
     "pico": EDU_PICO, "task_yaml": EDU_TASK, "refs": LUNG_REFS},
    {"mode": "both", "name": "技术 · 大模型高效微调对比",
     "desc": "非医学示例：AI 工程",
     "pico": TECH_PICO, "task_yaml": TECH_TASK, "refs": LUNG_REFS},
    {"mode": "both", "name": "社科 · 远程办公与生产率",
     "desc": "非医学示例：管理 / 经济",
     "pico": SOCIAL_PICO, "task_yaml": SOCIAL_TASK, "refs": LUNG_REFS},
    {"mode": "both", "name": "只看引用核验（真伪混杂）",
     "desc": "不跑综述，直接验证 3 条参考文献是否真实存在",
     "pico": "", "task_yaml": "", "refs": LUNG_REFS}
]


def live_task_yaml(text, sources):
    """Keep existing example topics; run them against real sources by default."""
    import re
    text = re.sub(r"(?m)^sources:.*$", "sources: [" + ", ".join(sources) + "]", text)
    text = re.sub(r"(?m)^max_papers:.*$", "max_papers: 12", text)
    for field in ("inclusion", "exclusion"):
        text = re.sub(r"(?m)^" + field + r":\s*\n(?:[ \t]+-.*\n)*", field + ": []\n", text)
    return text.rstrip() + "\nscreening_mode: topic\n"


def live_examples():
    import copy
    examples = copy.deepcopy(EXAMPLES)
    for item in examples:
        if not item.get('task_yaml'):
            continue
        sources = ['pubmed', 'openalex'] if item.get('mode') == 'pro' else ['openalex', 'semantic_scholar']
        item['task_yaml'] = live_task_yaml(item['task_yaml'], sources)
    examples.sort(key=lambda x: 0 if '低剂量CT' in x.get('name', '') else 1 if x.get('mode') == 'pro' else 2)
    return examples

