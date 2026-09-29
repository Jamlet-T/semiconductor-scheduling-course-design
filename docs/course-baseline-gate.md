# Course Baseline Gate：受限场景的课程设计基线实验

状态：`not_started`（2026-09-29）。本 Gate 独立于 [SMT2020 Data Integration Gate](smt2020-data-integration-gate.md)；后者仍为 `not_passed_gaps`。M1 已通过不自动使本 Gate 通过。

## 目标与可声称范围

在已验证 DES 内对 FIFO、SPT、EDD、CR 做公平、可复现的课程设计对照。允许以 SMT2020 的真实行参数构造**受限教学 benchmark**，但必须称为“SMT2020 参数驱动的受限场景”，不能称为完整 HVLM/LVHM、真实 Fab KPI 或全路线复现。已有一机一 lot 诊断 slice 仅用于 loader/runtime smoke，样本量不足以比较策略。

## 进入基线对比前的验收条件

1. 场景规格固定：所选 raw 文件/行号和 manifest hash、保留的产品/工序/机台、lot cohort 或生成规则、时域、初始状态、随机分布及种子均写入版本化配置与每次 run provenance。真实参数、人工生成的 workload 和 E 级假设分别标注。
2. 逐项列出原数据存在但场景未执行的 setup、batch、cascade、rework、sampling、CQT、dedication、failure/PM、release、transport、装卸等机制；不得 silent fallback。任何保留的机制必须有对应测试和受限真实记录核对。
3. 四策略调用同一个 `simulate(theta, scenario, seed)`、相同可行动作过滤、相同场景和配对种子；随机账本/CRN、provenance 与指标审计均通过。决策不得使用未来随机 realization。
4. 先以少量种子做 pilot，检查至少有真实派工竞争、策略选择可能不同、终端 WIP 与未完成 lot 被报告、时域与计算成本可接受。若策略输出相同，要如实报告或调整**事先声明**的教学 workload，不能事后只挑有利结果。
5. 核心结果至少包括吞吐、周期/等待、延期、设备利用与终端 WIP；区别初始 WIP 与新投放 cohort，并说明 warm-up 或不做 warm-up 的理由。正式对比的种子数及统计区间在 pilot 后冻结，不以单次运行宣称优劣。

## Exit Criteria

场景配置和来源清单已冻结；四策略的同场景配对运行可复现；审计与 CRN 检查通过；指标口径和不确定性已报告；报告清楚写明“受限教学场景”及所有省略项。只有这些条件满足，状态才可改为 `passed_limited`。这**不改变**完整 SMT2020 Data Integration Gate 的状态，也不授权自动开启 CMA-ES。

当前下一步：从现有静态模型中选择能形成多 lot 派工竞争的机制闭包；先做只读候选审计，不用单 lot smoke 代替实验设计。

## 首个候选（只读审计，尚未冻结或运行）

HVLM 的 `DE_BE_50` 有两个带真实 initial WIP 的、无本工序 setup/batch/sampling/rework/CQT/dedication/cascade interval 的 per-lot 工序：`route_3.txt:395` 的 `r_3:394` 为 `uniform(69.876, 3.4938) min`，`route_4.txt:258` 的 `r_4:257` 为 `uniform(173.106, 8.66) min`；分别有 9 个当前 WIP lot。`tool.txt.1l:6` 给 `STNQTY=9`、LOAD/UNLOAD 各 1 min、`STNCAP` 空、位置 Fab。`WIP.txt:278,1577` 是两组中的可追溯样例。两类加工时长不同，因而比单 lot smoke 更有潜力检验 SPT 与其他规则的差异。LVHM 存在同两条 route 工序，`tool.txt.1l:6` 为 10 台；其 WIP cohort 尚待单独核对。

该候选设备还附着 Dry_Etch Failure（`attach.txt:5`）和三条 wafer PM（`attach.txt:104-106`）。若教学场景只选部分物理机、只保留当前工序或省略 Failure/PM，必须在配置中逐项声明，这是**派生教学 benchmark**，不是原始 HVLM 的真实全厂子系统。原始 DUE 距仿真零点数日，不应为了制造延期效果暗改；需先评估原始交期与拟定时域是否匹配，再冻结指标和 cohort。下一步先完成这一候选的机制排除表与可行派工竞争检查，不生成正式策略结论。
