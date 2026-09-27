# SMT2020 多日历附件静态展开审计

状态：**静态映射已核对；运行时未闭环；Data Integration Gate 保持 `not_passed_gaps`。**

## 原始证据与影响范围

两套模型的 `attach.txt` 各有 303 条附件行：11 条 `down`、79 条按日历触发的 PM、213 条按加工片数触发的 PM。`RESTYPE=stngrp/stnfam` 必须先结合 `tool.txt.1l` 的 `STNGRP/STNFAM/STNQTY` 展开到具体物理机；303 是附件行数，**不是**受影响机台数。`pmcal.txt` 提供各 PM 的间隔或片数阈值及维修时长。

| 模型 | 生产物理机 | 附件展开边数 | 每机 2 条 calendar PM | 每机 3 条 calendar PM | 每机 3 条 wafer PM |
| --- | ---: | ---: | ---: | ---: | ---: |
| HVLM | 1043 | 4024 | 148 | 203 | 692 |
| LVHM | 913 | 3515 | 137 | 170 | 606 |

两模型所有生产机各有 1 条 failure attachment；有多条 calendar PM 的物理机分别为 351/307 台。原始可追溯示例：`attach.txt` 中 `DefMet_BE_33_MN/QT` 两行、`Litho_BE_110_WK/MN/QT` 三行，分别连接同名 `pmcal.txt` 条目和 `tool.txt.1l` 的具体设备模板。以上数字由 loader 的逐机静态展开统计与真实数据测试核对，不代表这些事件已在 DES 中正确并行运行。

## 尚未冻结的运行时组合

当前 `Scenario`、`FailureSchedule`、`PMRuntime` 将同机同类 calendar 视作单值；单一 downtime owner 的既有规则也没有定义 PM-PM 同刻时稳定优先级、占用期间到达的另一 PM 是失效还是延期，以及被抑制 stream 的下一周期如何起算。原始附件只证明“同时附着”，不定义这些冲突行为。必须先在 Simulation Contract 中标记本地假设，再用 synthetic 重叠/同刻/故障抢占微型测试和真实附件 slice 闭环，不能直接删除 `DI_UNSUPPORTED_MULTI_CALENDAR_ATTACHMENT`。

本轮仅强化 `raw → static model → physical-machine audit`，不构造完整 Scenario，不启动正式实验，`optimizer_enabled=false`，`datasets/` 不修改。
