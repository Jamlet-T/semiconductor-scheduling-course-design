# M1 金标准微型算例

`cases.json` 是 `docs/m1-simulation-reliability-baseline.md` 的机器可读版本，用于后续驱动仿真测试。当前只冻结输入和期望结果；仿真器尚未实现，不能把这些 fixture 的存在称为测试通过。

约定：

- 时间单位为分钟，数量单位显式写为 `wafers` 或 `lots`。
- 未声明的搬运时间为 0，加工时间为确定值。
- 同刻事件优先级引用 Simulation Contract 0.1.0；机器可读版本记录在 `event_priority_contract_version`。
- 数值比较默认精确；仅统计量可在实现时配置浮点容差。
- 每个 case 只突出一种机制，避免多个未决语义互相掩盖。
