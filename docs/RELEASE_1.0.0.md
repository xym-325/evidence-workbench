# 循证科研工作台 1.0.0

第一个 Windows 桌面版本：从研究问题、真实文献检索到带逐段摘要依据的中文初稿。

## 下载

- **普通用户**：`EvidenceWorkbench-1.0.0-windows-x64.zip`。完整解压后双击 `EvidenceWorkbench.exe`，无需安装 Python。
- **源码开发者**：GitHub 自动生成的 Source code，并额外下载 `mesh-2026.sqlite.gz`；按 README 核验和安装词表。
- **校验**：`SHA256SUMS.txt`。

支持 Windows 10/11 x64，需要 Microsoft WebView2 Runtime。AI 初稿功能需要本机 Claude Code 账号；软件不附带共享账号、免费额度或密钥。其他检索与人工复核功能可以独立使用。

## 体验顺序

先打开任一明确标记的真实案例回放，搜索文献，展开摘要与逐段引用依据，再导出证据包。需要新的研究结果时，载入或编辑任务卡，执行实时检索，再选择 AI 整理。

## 范围

摘要级研究辅助；不宣称完成全文分析、医学证据确定性评级或临床决策验证。推荐数量不等于证据充足。

个人研究数据位于 `%LOCALAPPDATA%\EvidenceWorkbench`，与程序文件分开；更新程序时保留该目录。
