# Third-party notices

本发行版包含独立开发的工作台整合与桌面封装，并沿用原项目中的 MeSH 映射、学术智能体任务解析/检索/核验及 SQ3R 阅读规范。原项目代码的作者权利予以保留；没有以本次发布替原作者选择 MIT 等额外开源授权。

## Application dependencies

- Python runtime — [Python Software Foundation License](https://docs.python.org/3/license.html)
- pywebview — [BSD-3-Clause](https://github.com/r0x0r/pywebview/blob/master/LICENSE)
- pythonnet — [MIT](https://github.com/pythonnet/pythonnet/blob/master/LICENSE)
- clr-loader, cffi, pycparser, proxy-tools, Bottle, typing-extensions — applicable notices are retained from their installed distributions under the packaged `licenses` directory.
- PyYAML — [MIT](https://github.com/yaml/pyyaml/blob/main/LICENSE)
- PyInstaller (build tool) — [GPL with bootloader exception](https://pyinstaller.org/en/stable/license.html)

Windows WebView2 Runtime is supplied by Microsoft or installed separately from its official distribution; this package does not modify or relicense it.

## Data resources

MeSH vocabulary and XML sources: [U.S. National Library of Medicine](https://nlmpubs.nlm.nih.gov/projects/mesh/MESH_FILES/xmlmesh/). The local SQLite index is distributed as a separate versioned resource and embedded in the desktop package for offline term lookup.

The two curated demonstrations contain public scholarly metadata and abstracts returned by PubMed/OpenAlex, plus explicitly labeled machine-generated drafts. Source links and retrieval provenance remain available. Inclusion of a source record does not grant rights to an article's full text or imply endorsement by NLM, publishers or database operators. No paid full texts are included.
