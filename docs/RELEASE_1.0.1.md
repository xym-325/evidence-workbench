# 循证科研工作台 1.0.1

修正 1.0.0 增加的 Claude Code 运行依赖，移除后来添加的“AI 生成中文综述初稿”功能及其后端调用。无需安装 Claude Code 或其他 AI CLI，无需模型账号或 API Key。

保留原有综述材料流程，以及任务卡、MeSH、真实文献检索、筛选理由、人工复核、引用核验、历史记录和导出。案例回放保留真实题录与检索记录，使用原有材料章节。

## 使用

下载 `EvidenceWorkbench-1.0.1-windows-x64.zip`，完整解压后双击 `EvidenceWorkbench.exe`。支持 Windows 10/11 x64，无需 Python，需要 Microsoft WebView2 Runtime。

新检索与外部题录核验需要联网；已保存案例可以离线回放。综述材料基于已返回摘要，需人工核查。

## 更新

关闭旧程序，解压新版本并启动。研究记录保存在 `%LOCALAPPDATA%\EvidenceWorkbench`，无需删除或重新导入。旧版本历史记录保持可读取。

普通用户无需单独下载词表；源码开发者使用 `scripts/fetch_mesh.py`，继续从 v1.0.0 获取固定版本的 MeSH 资源。`SHA256SUMS.txt` 中包含程序压缩包和该词表的校验值。
