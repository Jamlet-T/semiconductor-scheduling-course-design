# 文档目录

本目录用于项目实现过程中的技术说明、数据字典、模型假设和实验记录。新增文档应标明状态、来源和适用版本。

当前正式文档：

- [Simulation Contract](simulation-contract.md)：事件、约束、指标和随机数的统一契约。
- [Data Contract](data-contract.md)：SMT2020 原始字段到内部对象及事件语义的映射。
- [可信轻量 DES 架构](des-architecture.md)：MC01～MC07 内核、事件流、状态机与验证边界。
- [M1 — Simulation Reliability Baseline](m1-simulation-reliability-baseline.md)：进入优化前的强制验收门槛与金标准算例。
- [PySCFabSim Semantic Diff](pyscfabsim-semantic-diff.md)：开源仿真器源码语义审计。

后续继续记录策略配置与实验复现步骤。当前已完成 MC01～MC07 的微型验证，没有 HVLM/LVHM 正式实验结果；不得将计划内容写成已验证结论。SMT2020 数据随仓库位于根目录 `datasets/`，已纳入版本控制的数据保持内容只读、原始字节不可变；派生数据写入 `runs/` 或 `artifacts/`。原始教学资料仍需通过教师或组内授权渠道取得，不在公开仓库保存。
