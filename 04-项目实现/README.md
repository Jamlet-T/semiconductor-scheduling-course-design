# fab-scheduler

这是半导体制造系统智能调度课程设计的 Python 包。项目周期为 14 周，目前处于 `M1 — Simulation Reliability Baseline`：最小 DES 已验证 MC01、MC02，包含 FIFO、逐事件 trace 与 provenance；MC03～MC08 和正式数据实验尚未实现，CMA-ES 在 M1 通过前禁用。

通过仓库根目录的命令安装：

```powershell
python -m pip install -e .\04-项目实现
python -m fab_scheduler --help
python -m fab_scheduler info
```

本包当前没有第三方运行依赖。构建使用 setuptools。SMT2020 数据随公开仓库克隆获取，位于仓库根目录 `datasets/`（相对本目录为 `../datasets`）。已纳入版本控制的数据保持内容只读、原始字节不可变；派生数据写入 `runs/` 或 `artifacts/`。原始教学资料与数据约定见仓库根目录 README。
