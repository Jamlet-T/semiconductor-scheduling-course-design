# 半导体制造系统的智能调度

面向 14 周课程设计的小组协作仓库，目标是围绕 SMT2020 数据建立可复现的晶圆厂调度研究与软件原型。

项目入口：[完整实施方案与 14 周安排](03-实施方案/课程设计完整实施方案.md) · [Simulation Contract](docs/simulation-contract.md) · [Data Contract](docs/data-contract.md) · [M1 验收基线](docs/m1-simulation-reliability-baseline.md) · [PySCFabSim 语义审计](docs/pyscfabsim-semantic-diff.md) · [数据结构审计](03-实施方案/数据结构审计.json) · [贡献指南](CONTRIBUTING.md)。

## 当前状态

**已实现：** Python 3.11+ 的最小可安装包、Data Contract、事件日历、动态投放、设备/队列状态、FIFO 派工、确定性加工、路线推进、显式 Setup、wafer 容量 Batch、主动批等待唤醒、实体索引随机流、provenance 和事件 trace。MC01～MC04 已逐事件验证；MC05～MC08、高级机制和正式数据实验尚未实现。

**计划中：** 后续按 CQT → Dedication → Failure/PM 逐项解锁剩余 micro case；8 个算例全部通过后再运行 HVLM/LVHM，M1 通过后才允许 CMA-ES。当前没有正式数据实验结果、事件回放前端或真实设备接入。

## 数据与教学材料

SMT2020 原始数据位于仓库根目录 `datasets/`，并随公开仓库克隆获取；从 `04-项目实现/` 目录访问时路径为 `../datasets`。已纳入版本控制的 `datasets/` 内容保持只读，原始字节不可变；清洗结果和实验输出写入 `runs/` 或 `artifacts/` 等生成目录。原始教学资料和分组信息仍不公开，`00-指导文件/`、`01-模板/` 和 `02-汇报/` 继续由 Git 忽略；其中包括分组与选题表、任务书、PDF、Word、PowerPoint、Excel 等课程材料。克隆后如需这些材料，请通过教师或组内授权渠道获取。不得提交额外未授权数据或教学文件。

## 环境与运行

需要 Python 3.11 或 3.12，无第三方运行依赖。以下命令在 Windows PowerShell 中从仓库根目录执行：

```powershell
py -3.11 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -e .\04-项目实现
python -m fab_scheduler --help
python -m fab_scheduler info
```

运行脚手架自带的测试：

```powershell
python -m unittest discover -s .\04-项目实现\tests -v
```

## 项目结构

```text
04-项目实现/
  pyproject.toml
  src/fab_scheduler/
    data/ domain/ simulation/ policies/ optimization/
    evaluation/ services/ api/
  tests/
datasets/ (SMT2020 原始数据；从 04-项目实现/ 访问为 ../datasets)
configs/project.json
docs/README.md
```

各模块目前是后续开发的占位入口。项目阶段和 14 周周期也记录在 `configs/project.json`。

## 协作约定

请先阅读 [贡献指南](CONTRIBUTING.md)。日常工作从 `main` 更新，使用 `feature/<topic>` 分支提交小范围改动，通过 Pull Request 合并；不要直接向 `main` 推送。问题、决策和待办使用 GitHub Issues 记录。
