# 贡献指南

本项目由小组在 14 周课程设计周期内协作完成。提交的代码、配置和说明应便于其他组员复现，并清楚标注已完成内容与计划内容。

## 分支与 Pull Request

1. 从最新 `main` 创建 `feature/<topic>` 分支，例如 `feature/cli-info`。
2. 一次改动聚焦一个任务，提交信息简要说明变更。
3. 推送分支并创建 Pull Request；描述目的、实现内容、验证命令和未解决事项。
4. 至少由一名组员检查后再合并到 `main`。不直接向 `main` 推送。

## 本地开发

在仓库根目录使用 Windows PowerShell：

```powershell
py -3.11 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -e .\04-项目实现
python -m fab_scheduler --help
python -m fab_scheduler info
python -m unittest discover -s .\04-项目实现\tests -v
```

当前包只依赖 Python 标准库。后续若确需增加依赖，应更新 `04-项目实现/pyproject.toml` 并在 Pull Request 中说明用途。

## 数据、配置与结果

- 已纳入版本控制的 `datasets/` 保持内容只读，原始字节不可变；不提交额外未授权数据。清洗和实验输出写入 `runs/` 或 `artifacts/` 等生成目录，并说明来源、配置和随机种子。`00-指导文件/`、`01-模板/` 和 `02-汇报/` 中的原始教学资料及分组信息不公开，通过教师或组内授权渠道获取。
- 原始数据只读。清洗和实验输出写入 `runs/` 或 `artifacts/` 等生成目录，并说明来源、配置和随机种子。
- 配置文件应保持可复现且不含敏感信息。凭证、访问令牌和个人数据不得写入仓库；本地密钥放在未跟踪的 `.env` 文件中。
- 不提交未经核验的结果，不把计划功能描述成已实现功能。

## 问题跟踪

使用 GitHub Issue 描述任务，写清目标、验收条件和依赖；完成后在 Pull Request 中关联对应 Issue。
