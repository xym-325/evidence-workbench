"""项目内固定路径与常量。

所有模块统一从这里取路径, 避免各处硬编码。
"""

from __future__ import annotations

from pathlib import Path
import sys

# MeSH 生产年份（决定原始文件名与索引里记录的版本）
MESH_YEAR = 2026

# mesh_query/ 的上一级 = 项目根 mesh-query/
PACKAGE_ROOT = Path(__file__).resolve().parent
PROJECT_ROOT = PACKAGE_ROOT.parent
if getattr(sys, 'frozen', False):
    PROJECT_ROOT = Path(sys._MEIPASS) / 'workspace' / '医工交叉' / 'mesh-query'

DATA_DIR = PROJECT_ROOT / "data"
RAW_DIR = DATA_DIR / "raw"
RECORDS_DIR = PROJECT_ROOT / "records"

# 官方原始数据（NLM XML 格式）
DESC_GZ = RAW_DIR / f"desc{MESH_YEAR}.gz"
QUAL_XML = RAW_DIR / f"qual{MESH_YEAR}.xml"

# 构建产物
DB_PATH = DATA_DIR / "mesh.db"

# 中文 -> 英文提示词表（人工整理, 运行时会用英文索引二次校验）
ZH_MAP_PATH = DATA_DIR / "zh_map.json"

# 映射记录
MAPPING_RECORD = RECORDS_DIR / "mappings.jsonl"

# NLM 官方下载地址
MESH_XML_BASE = f"https://nlmpubs.nlm.nih.gov/projects/mesh/MESH_FILES/xmlmesh/"
SOURCE_URLS = {
    f"desc{MESH_YEAR}.gz": MESH_XML_BASE + f"desc{MESH_YEAR}.gz",
    f"qual{MESH_YEAR}.xml": MESH_XML_BASE + f"qual{MESH_YEAR}.xml",
}
