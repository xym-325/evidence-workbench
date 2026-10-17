# -*- coding: utf-8 -*-
"""学术智能体（Academic Review Agent）v1。

CLI 入口：``python -m src.cli --request "..."``
流水线：parse_request -> plan_search -> fetch_literature -> screen_papers
        -> write_review -> verify_citations
"""
__version__ = "1.0.0"
