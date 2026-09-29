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
- [SMT2020 Rework Semantic Audit](smt2020-rework-semantic-audit.md)：返工 raw 关系、论文/固定参考的证据分层、sampling/dedication/initial-WIP 组合与仍保持的 rework blocker。
- [SMT2020 Load / Unload / Cascade Semantic Audit](smt2020-cascade-semantic-audit.md)：级联双时点、真实 1+1 分钟装卸、参考实现尾段行为与分阶段验收边界。
- [SMT2020 Non-cascade Load / Unload Runtime Audit](smt2020-load-unload-runtime-audit.md)：独立装卸阶段、真实两工序受限切片、审计证据及未关闭的级联边界。
- [SMT2020 Multi-calendar Attachment Audit](smt2020-multi-calendar-attachment-audit.md)：303 条附件行到生产物理机的静态展开、Calendar PM 子链诊断 slice，以及仍未关闭的同机多 calendar/full-fab blocker。
- [SMT2020 Setup MINRUN Audit](smt2020-setup-minrun-audit.md)：Implant_Gas 原始字段链、参考实现差异、初始历史缺失及运行时关闭条件。
- [SMT2020 Data Integration Gate](smt2020-data-integration-gate.md)：真实数据 reconciliation、validation smoke 与 DI-E01～DI-E15 判定。

M1 Closure Audit 已重新执行：MC01～MC08 与原始 E01～E10 全部 PASS，M1 状态为 `passed`。processing distribution/PTPER、exponential failure、transport、受限 release、真实 sampling profile、显式配置下的真实 batch 决策、non-cascade load/unload 两工序切片及 Calendar PM 单机单工序诊断 slice 均已形成各自受限证据链；其中 multi-calendar 受限回归为 `7 passed`，本轮 full regression 为 `294 passed`。Calendar PM slice 不加载 Failure/Wafer PM、其他机器、后续 route/rework 或初始历史，不代表 full-fab 集成测试通过。MINRUN、rework、load/unload/cascade 与多 calendar/full-fab 组合 blocker 未关闭。显式 v1 batch 配置下 Data Integration Gate 仍有 4 类 blocker、状态为 `not_passed_gaps`；默认无配置 audit 有 5 类。没有 HVLM/LVHM 正式实验结果，CMA-ES 继续禁用。当前契约为 Simulation `0.1.9`、Policy `0.1.1`、Loader `0.1.7`、Data `0.1.8`。
