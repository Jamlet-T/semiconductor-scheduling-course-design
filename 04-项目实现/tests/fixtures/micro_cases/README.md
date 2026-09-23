# M1 金标准微型算例

`cases.json` 是 `docs/m1-simulation-reliability-baseline.md` 的机器可读版本。MC01～MC08 已由轻量 DES 逐事件验证；E06/E07/E10 补齐并重新审计后，原 M1 十项门槛全部 PASS。SMT2020 Data Integration Gate 仍为 pending。

约定：

- 时间单位为分钟，数量单位显式写为 `wafers` 或 `lots`。
- 未声明的搬运时间为 0，加工时间为确定值。
- 同刻事件优先级引用 Simulation Contract 0.1.4；机器可读版本记录在 `event_priority_contract_version`。
- 数值比较默认精确；仅统计量可在实现时配置浮点容差。
- 每个 case 只突出一种机制，避免多个未决语义互相掩盖。
- `implementation_status=verified` 只在相应自动化测试同时检查 trace 和 KPI 后使用。
