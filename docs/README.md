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
- [SMT2020 Transport Runtime Audit](smt2020-transport-runtime-audit.md)：搬运事件、随机身份、真实 route pair 与缺失 pair 审计闭环。
- [SMT2020 Release Runtime Audit](smt2020-release-runtime-audit.md)：release template 的 raw 证据、惰性投放、namespaced stable ID、due 平移和受限支持边界。
- [SMT2020 Sampling Runtime Audit](smt2020-sampling-runtime-audit.md)：StepPercent 的 operation-entry skip、CRN、p100-CQT 边界、真实 initial-WIP 判定诊断 slice，以及独立的 load/unload 限制。
- [SMT2020 Data Integration Gate](smt2020-data-integration-gate.md)：真实数据 reconciliation、validation smoke 与 DI-E01～DI-E15 判定。

M1 Closure Audit 已重新执行：MC01～MC08 与原始 E01～E10 全部 PASS，M1 状态为 `passed`。processing distribution/PTPER、exponential failure、transport、受限 release 与真实 sampling profile 已形成各自证据链。sampled CQT targets（HVLM 4、LVHM 18）全为 p100，stochastic endpoint 为 0，因此 sampling blocker 已关闭；sampling 诊断 slice 未执行全部 sampled 工序共有的 1 分钟 load/unload，故 load/unload blocker仍保留。Data Integration Gate 现有 5 类 blocker，状态保持 `not_passed_gaps`；没有 HVLM/LVHM 正式实验结果，CMA-ES 继续禁用。当前契约为 Simulation `0.1.5`、Policy `0.1.1`、Loader `0.1.4`、Data `0.1.3`。
