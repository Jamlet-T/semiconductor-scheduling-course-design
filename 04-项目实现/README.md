# fab-scheduler

这是半导体制造系统智能调度课程设计的 Python 包。项目周期为 14 周。可信轻量 DES 已验证 MC01～MC08，并提供 FIFO/SPT/EDD/CR、统一 DispatchAction、`simulate(theta, scenario, seed)`、RandomSampleLedger/CRN audit、Setup、wafer Batch、CQT、Dedication、Failure、PM、外生无容量 Transport、受限 SMT2020 Release Template 与 operation-entry Sampling、trace、provenance 及独立审计检查器。Setup MINRUN 另有合成场景的本地硬约束能力，但未形成真实组合闭环。重新审计后 M1 E01～E10 全部 PASS。正式 loader 能生成加工、搬运、release、batch 的受限 validation slice 和 sampling 判定诊断 slice；multi-calendar attachment 已逐物理机静态展开。raw sampled CQT targets 4/18 全为 p100，sampling blocker 已关闭；真实 batch 决策在显式 v1 配置下通过 target/timeout 切片验证。batch/sampling 切片未执行相关设备 1+1 分钟 load/unload，batch 还省略 calendar。显式配置下完整 Data Integration Gate 仍有 rework、load-unload-cascade、setup MINRUN、multi-calendar 四类 blocker，状态为 `not_passed_gaps`；默认无配置 audit 仍有五类。课程设计现另设 [受限基线 Gate](../docs/course-baseline-gate.md)，可在明确来源和省略机制后独立验收教学场景，不能将其结果称为完整 SMT2020 实验。目前尚未开展正式基线对比，CMA-ES 继续禁用。

Loader 公共入口：

```python
from fab_scheduler.data import LoaderConfig, load_smt2020

loaded = load_smt2020("../datasets", "SMT2020_HVLM")
assert loaded.scenario is None  # BLOCKER 未清零，禁止伪造完整 Scenario

smoke = load_smt2020(
    "../datasets",
    "SMT2020_HVLM",
    loader_config=LoaderConfig(mode="validation_slice"),
)

transport_smoke = load_smt2020(
    "../datasets",
    "SMT2020_HVLM",
    loader_config=LoaderConfig(mode="transport_validation_slice"),
)

sampling_smoke = load_smt2020(
    "../datasets",
    "SMT2020_HVLM",
    loader_config=LoaderConfig(mode="sampling_validation_slice"),
)
```

通过仓库根目录的命令安装：

```powershell
python -m pip install -e .\04-项目实现
python -m fab_scheduler --help
python -m fab_scheduler info
```

本包当前没有第三方运行依赖。构建使用 setuptools。SMT2020 数据随公开仓库克隆获取，位于仓库根目录 `datasets/`（相对本目录为 `../datasets`）。已纳入版本控制的数据保持内容只读、原始字节不可变；派生数据写入 `runs/` 或 `artifacts/`。原始教学资料与数据约定见仓库根目录 README。
