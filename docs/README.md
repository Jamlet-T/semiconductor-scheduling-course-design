# 文档目录

本目录用于项目实现过程中的技术说明、数据字典、模型假设和实验记录。新增文档应标明状态、来源和适用版本。

当前正式文档：

- [Simulation Contract](simulation-contract.md)：事件、约束、指标和随机数的统一契约。
- [Data Contract](data-contract.md)：SMT2020 原始字段到内部对象及事件语义的映射。
- [可信轻量 DES 架构](des-architecture.md)：MC01～MC08 内核、事件流、状态机与验证边界。
- [M1 — Simulation Reliability Baseline](m1-simulation-reliability-baseline.md)：进入优化前的强制验收门槛与金标准算例。
- [M1 Closure Audit](m1-closure-audit.md)：原始 Exit Criteria、三向追踪、PASS/GAP 判定与下一 Gate。
- [Dispatch Policy Contract](dispatch-policy-contract.md)：FIFO/SPT/EDD/CR、统一 Action/API 和 CRN 语义。
- [Metric Contract](metric-contract.md)：终止模式、指标分母、WIP、剩余工作和时间守恒口径。
- [Semantic Evidence Matrix](semantic-evidence-matrix.md)：原始数据、参考实现、本地假设和不可恢复历史状态的证据分级。
- [PySCFabSim Semantic Diff](pyscfabsim-semantic-diff.md)：开源仿真器源码语义审计。
- [SMT2020 Loader Contract](smt2020-loader-contract.md)：manifest、raw 字段到静态领域模型/Scenario 的映射及 blocker 规则。
- [SMT2020 Data Integration Gate](smt2020-data-integration-gate.md)：真实数据 reconciliation、validation smoke 与 DI-E01～DI-E15 判定。

M1 Closure Audit 已重新执行：MC01～MC08 与原始 E01～E10 全部 PASS，M1 状态为 `passed`。SMT2020 Data Integration Gate 已完成首轮正式审计，因真实 processing/release/transport/sampling/rework/cascade/calendar runtime 缺口为 `not_passed_gaps`；因此没有 HVLM/LVHM 正式实验结果，CMA-ES 继续禁用。SMT2020 数据随仓库位于根目录 `datasets/`，已纳入版本控制的数据保持内容只读、原始字节不可变；派生数据写入 `runs/` 或 `artifacts/`。原始教学资料仍需通过教师或组内授权渠道取得，不在公开仓库保存。
