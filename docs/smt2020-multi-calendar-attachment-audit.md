# SMT2020 多日历附件静态展开审计

状态：**静态映射已核对；运行时未闭环；Data Integration Gate 保持 `not_passed_gaps`。**

## 原始证据与影响范围

两套模型的 `attach.txt` 各有 303 条附件行：11 条 `down`、79 条按日历触发的 PM、213 条按加工片数触发的 PM。`RESTYPE=stngrp/stnfam` 必须先结合 `tool.txt.1l` 的 `STNGRP/STNFAM/STNQTY` 展开到具体物理机；303 是附件行数，**不是**受影响机台数。`pmcal.txt` 提供各 PM 的间隔或片数阈值及维修时长。

| 模型 | 生产物理机 | 附件展开边数 | 每机 2 条 calendar PM | 每机 3 条 calendar PM | 每机 3 条 wafer PM |
| --- | ---: | ---: | ---: | ---: | ---: |
| HVLM | 1043 | 4024 | 148 | 203 | 692 |
| LVHM | 913 | 3515 | 137 | 170 | 606 |

两模型所有生产机各有 1 条 failure attachment；有多条 calendar PM 的物理机分别为 351/307 台。原始可追溯示例：`attach.txt` 中 `DefMet_BE_33_MN/QT` 两行、`Litho_BE_110_WK/MN/QT` 三行，分别连接同名 `pmcal.txt` 条目和 `tool.txt.1l` 的具体设备模板。以上数字由 loader 的逐机静态展开统计与真实数据测试核对，不代表这些事件已在 DES 中正确并行运行。

逐机联合分布进一步确认：HVLM/LVHM 分别有 692/606 台为 `(failure=1, calendar PM=0, wafer PM=3)`，148/137 台为 `(1,2,0)`，203/170 台为 `(1,3,0)`；当前 raw **没有**同机同时附着 calendar PM 与 wafer PM 的组合。calendar PM 的真实间隔为 7/30/91 day，单机的两条或三条 PM 仍可能在运行中同刻或重叠；静态互斥不能据此推断冲突处理规则。

## 尚未冻结的运行时组合

当前 `Scenario`、`FailureSchedule`、`PMRuntime` 将同机同类 calendar 视作单值；单一 downtime owner 的既有规则也没有定义 PM-PM 同刻时稳定优先级、占用期间到达的另一 PM 是失效还是延期，以及被抑制 stream 的下一周期如何起算。原始附件只证明“同时附着”，不定义这些冲突行为。必须先在 Simulation Contract 中标记本地假设，再用 synthetic 重叠/同刻/故障抢占微型测试和真实附件 slice 闭环，不能直接删除 `DI_UNSUPPORTED_MULTI_CALENDAR_ATTACHMENT`。

扩展时必须区分“多个 PM 来源”和“单一 active downtime owner”：现有 `PMRuntime` 按 `machine_id` 索引单条 PM，直接塞入多条会覆盖旧来源。每条展开后的 PM 还需以 `calendar_id + physical_machine_id` 等稳定复合身份生成独立随机流，不能只用可被多台机共享的 raw calendar 名称。上述是架构必要条件，不是已经冻结的 PM-PM 物理语义。

本轮仅强化 `raw → static model → physical-machine audit`，不构造完整 Scenario，不启动正式实验，`optimizer_enabled=false`，`datasets/` 不修改。
