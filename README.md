# 天然气管理知识工具

版本：**v1.4.0**

面向天然气企业管理资料的桌面知识治理工具。它把制度资料、管理问题、解决措施、事件分析和记忆训练连接起来，形成“资料收集 → 自动处理 → 人工审核 → 知识沉淀 → 检索复用 → 周期治理”的闭环。

## 主要功能

- 资料收集：支持 PDF、DOCX、TXT、MD、CSV、XLSX；文件夹批量选择支持 Ctrl/Shift 多选。
- OCR：扫描 PDF 没有文字层时，调用 Windows 中文 OCR。
- AI 工具：支持 DeepSeek、豆包和本地规则版；日志区分实际调用和规则版回退。
- 人工审核：显示原始材料、原文依据、问题判断、管理内涵、领域和属性；未选择按不同意处理。
- 知识沉淀：自动生成原始资料库、分类台账、管理要点、事件分析和记忆卡片。
- 知识检索：按关键词检索，并按领域、属性和文字相似度汇总同类问题。
- 智能记忆训练：待审核卡片、间隔重复、掌握度、薄弱清单和提醒。
- 周期治理：新增/修改/删除/重复检测、待审核草稿、周报、月报和哈希校验发布。
- v1.1 改进：周期草稿可视化审核、原子写入、任务审计、SQLite 可重建索引、版本快照差异、相似资料识别和知识库备份恢复。
- v1.2 改进：统一任务编号与状态、批量进度/暂停/取消/恢复、AI与规则版调用审计、OCR质量提示、配置校验与环境变量密钥、SQLite迁移、证据检索、分类筛选、趋势统计、轻量语义检索、跨文档关联和专题报告。
- v1.3 改进：统一任务中心、OCR页级质量复核、Windows DPAPI密钥存储、关键词与语义混合检索、问题归并建议及人工批准发布。
- v1.4 改进：新增系统迁移包，支持知识库、训练记录、任务状态、规则反馈和脱敏配置的打包、校验与恢复；API Key 不进入迁移包。

## 快速开始

### 使用 Windows 可执行程序

运行 `dist/天然气管理工具.exe`。首次使用时，在“系统设置”中选择知识库目录；如需 AI，再填写对应 API Key。

### 使用源码

需要 Python 3.10 或更高版本：

```powershell
python -m pip install -r requirements.txt
Copy-Item config.example.json config.json
python main.py
```

规则版不需要网络或 API Key，可以先完成完整流程。API Key 可使用 `WESTGAS_DEEPSEEK_API_KEY` 或 `WESTGAS_DOUBAO_API_KEY` 环境变量，避免写入配置文件。

## 文档

- [完整使用说明](使用说明.md)
- [版本更新记录](CHANGELOG.md)
- [周期治理实现说明](docs/周期治理说明.md)

## 测试

```powershell
python -m py_compile main.py process.py ai_tools.py search.py knowledge_db.py task_manager.py
python -m unittest discover -s tests -v
```

## 构建 Windows EXE

```powershell
python -m PyInstaller --clean --noconfirm "天然气管理工具.spec"
```

生成文件位于 `dist/天然气管理工具.exe`。构建目录和用户知识库不会提交到 GitHub。

## 数据与安全边界

- 未配置 AI 时，资料只在本地规则版处理。
- 选择 DeepSeek 或豆包后，资料内容会发送到相应服务商接口，使用前应遵守企业数据和保密要求。
- 任何 AI 或规则版结论都需要人工审核后才能进入正式台账。
- 发布流程会检查原始资料哈希，原始资料在审核后发生变化时自动阻止发布。

## 许可证

当前版本暂未指定开源许可证。公开发布前请根据单位授权和代码归属补充许可证文件。
