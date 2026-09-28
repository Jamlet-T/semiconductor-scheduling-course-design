"""同机多 Calendar PM 的合成验收测试。

这些测试只依据 ``docs/simulation-contract.md`` 中冻结的同刻事件顺序和
PM 语义，不把 SMT2020 原始数据解释成额外的 PM 优先级。当前仓库若仍在
Data Integration blocker 阶段，涉及同机多 Calendar PM 的测试可以先红灯；
红灯本身用于固定尚未实现的边界，不应通过放宽断言或静默丢弃 PM 来消除。
"""

from __future__ import annotations

from pathlib import Path
import sys
import unittest

SOURCE_ROOT = Path(__file__).resolve().parents[1] / "src"
sys.path.insert(0, str(SOURCE_ROOT))

from fab_scheduler.domain.models import (  # noqa: E402
    CalendarPMSpec,
    LotSpec,
    MachineFailureSpec,
    MachineSpec,
    OperationSpec,
    Scenario,
    ScriptedFailureSpec,
    ScriptedPMSpec,
    TimeDistributionSpec,
    WaferPMSpec,
)
from fab_scheduler.simulation.engine import Simulator  # noqa: E402
from fab_scheduler.simulation.events import EVENT_PRIORITIES, EventType  # noqa: E402
from fab_scheduler.simulation.pm import PMRuntime  # noqa: E402
from fab_scheduler.simulation.random_streams import EntityRandomStreams  # noqa: E402


def _operation(duration: float = 20, *, machine: str = "M1") -> OperationSpec:
    return OperationSpec(1, duration, (machine,), route_id="MULTI-PM")


def _scripted_pm(
    pm_id: str,
    *occurrences: tuple[float, float],
    machine: str = "M1",
) -> CalendarPMSpec:
    return CalendarPMSpec(
        pm_id,
        machine,
        "scripted",
        scripted_occurrences=tuple(
            ScriptedPMSpec(start, duration)
            for start, duration in occurrences
        ),
    )


def _periodic_pm(
    pm_id: str,
    *,
    first_start: float,
    interval: float | TimeDistributionSpec,
    duration: float | TimeDistributionSpec,
    machine: str = "M1",
) -> CalendarPMSpec:
    interval_spec = (
        interval
        if isinstance(interval, TimeDistributionSpec)
        else TimeDistributionSpec("constant", interval)
    )
    duration_spec = (
        duration
        if isinstance(duration, TimeDistributionSpec)
        else TimeDistributionSpec("constant", duration)
    )
    return CalendarPMSpec(
        pm_id,
        machine,
        "periodic",
        first_start_time=first_start,
        interval=interval_spec,
        duration=duration_spec,
    )


def _failure(machine: str, failure_time: float, repair_duration: float) -> MachineFailureSpec:
    return MachineFailureSpec(
        machine,
        "scripted",
        scripted_failures=(ScriptedFailureSpec(failure_time, repair_duration),),
    )


def _scenario(
    *,
    calendar: tuple[CalendarPMSpec, ...] = (),
    failures: tuple[MachineFailureSpec, ...] = (),
    horizon: float | None = None,
    load: float = 0,
    unload: float = 0,
    operation_duration: float = 20,
    wafer_pm: tuple[WaferPMSpec, ...] = (),
    machines: tuple[MachineSpec, ...] | None = None,
) -> Scenario:
    configured_machines = machines or (
        MachineSpec("M1", load_minutes=load, unload_minutes=unload),
    )
    return Scenario(
        "SYNTH_MULTI_CALENDAR_PM",
        "synthetic@multi-calendar-pm-0.1.0",
        configured_machines,
        (LotSpec("L1", 0, (_operation(operation_duration),)),),
        termination_mode="fixed_horizon" if horizon is not None else "until_all_complete",
        horizon=horizon,
        failure_specs=failures,
        calendar_pm_specs=calendar,
        wafer_pm_specs=wafer_pm,
    )


def _same_time_pm_records(result, time: float):
    return [
        record
        for record in result.trace
        if record.sim_time == time
        and record.event_type in {"PM_START", "PM_START_STALE"}
    ]


class MultiCalendarPMAcceptanceTests(unittest.TestCase):
    def test_same_time_calendar_pm_has_one_owner_and_is_input_order_independent(self) -> None:
        """同刻 PM 同类事件按稳定序列处理，不因配置 tuple 顺序改变。"""
        pm_a = _scripted_pm("PM-A", (0, 4), (10, 1))
        pm_b = _scripted_pm("PM-B", (0, 2), (10, 1))

        forward = Simulator(
            _scenario(calendar=(pm_a, pm_b), operation_duration=25),
            seed=11,
        ).run()
        reverse = Simulator(
            _scenario(calendar=(pm_b, pm_a), operation_duration=25),
            seed=11,
        ).run()

        # The same stable entity/event sequence must be observable regardless
        # of the input tuple order; silently dropping one Calendar PM is not a
        # valid implementation of this contract.
        self.assertEqual(forward.trace_as_dicts(), reverse.trace_as_dicts())
        self.assertEqual(forward.pm_intervals, reverse.pm_intervals)
        self.assertEqual(forward.completion_times, reverse.completion_times)
        self.assertEqual(forward.provenance.simulation_contract_version, "0.1.8")
        self.assertEqual(
            forward.provenance.simulation_config["pm_runtime_schema_version"],
            "0.1.1",
        )

        for result in (forward, reverse):
            for time in (0, 10):
                records = _same_time_pm_records(result, time)
                self.assertEqual(
                    [record.event_type for record in records],
                    ["PM_START", "PM_START_STALE"],
                )
                self.assertEqual(
                    [record.state_after for record in records],
                    ["AVAILABILITY:DOWN", "NO_EFFECT"],
                )
                self.assertEqual(
                    [record.pm_id for record in records],
                    ["PM-A", "PM-B"],
                )
                self.assertLess(
                    records[0].cause_event_seq,
                    records[1].cause_event_seq,
                )

    def test_overlapping_second_calendar_pm_is_stale_and_periodic_occurrence_does_not_drift(self) -> None:
        """重叠 PM 的第二 occurrence 无效，但下一 occurrence 仍按计划时刻生成。"""
        primary = _periodic_pm(
            "PM-A",
            first_start=0,
            interval=10,
            duration=6,
        )
        overlapping = _periodic_pm(
            "PM-B",
            first_start=5,
            interval=10,
            duration=2,
        )

        result = Simulator(
            _scenario(calendar=(primary, overlapping), horizon=16),
            seed=12,
        ).run()

        stale_b = [
            record
            for record in result.trace
            if record.event_type == "PM_START_STALE" and record.pm_id == "PM-B"
        ]
        self.assertEqual(
            [(record.sim_time, record.pm_occurrence_index) for record in stale_b],
            [(5, 0), (15, 1)],
        )
        self.assertNotIn(17, [record.sim_time for record in stale_b])
        self.assertEqual(
            [(item.pm_id, item.occurrence_index, item.start, item.finish) for item in result.pm_intervals],
            [("PM-A", 0, 0, 6), ("PM-A", 1, 10, 16)],
        )

    def test_pm_finish_precedes_same_time_pm_start(self) -> None:
        """PM_FINISH 与 PM_START 同刻遵守冻结的事件优先级。"""
        periodic = _periodic_pm(
            "PM-A",
            first_start=0,
            interval=3,
            duration=3,
        )
        result = Simulator(_scenario(calendar=(periodic,), horizon=3)).run()

        at_three = [
            record.event_type
            for record in result.trace
            if record.sim_time == 3
            and record.event_type in {"PM_FINISH", "PM_START"}
        ]
        self.assertEqual(at_three, ["PM_FINISH", "PM_START"])
        self.assertLess(
            EVENT_PRIORITIES[EventType.PM_FINISH],
            EVENT_PRIORITIES[EventType.PM_START],
        )
        self.assertEqual(result.pm_count, 2)

    def test_failure_owns_machine_before_same_time_calendar_pm(self) -> None:
        """Failure/PM 同刻时，Failure 取得唯一停机所有权。"""
        result = Simulator(
            _scenario(
                calendar=(_scripted_pm("PM-A", (0, 2)),),
                failures=(_failure("M1", 0, 3),),
                horizon=3,
            )
        ).run()

        at_zero = [
            record.event_type
            for record in result.trace
            if record.sim_time == 0
            and record.event_type in {"FAILURE_START", "PM_START_STALE"}
        ]
        self.assertEqual(at_zero, ["FAILURE_START", "PM_START_STALE"])
        self.assertEqual(result.failure_count, 1)
        self.assertEqual(result.pm_count, 0)
        self.assertLess(
            EVENT_PRIORITIES[EventType.FAILURE_START],
            EVENT_PRIORITIES[EventType.PM_START],
        )

    def test_pm_preemptively_resumes_processing_and_each_load_unload_phase(self) -> None:
        """PM 中断 core、LOAD、UNLOAD 都按剩余时长续作，不重启。"""
        for stage, start in {
            "LOAD": 0.5,
            "PROCESS": 3,
            "UNLOAD": 6.5,
        }.items():
            with self.subTest(stage=stage):
                scenario = _scenario(
                    calendar=(_scripted_pm("PM-A", (start, 2)),),
                    operation_duration=5,
                    load=1,
                    unload=1,
                )
                result = Simulator(scenario, seed=13).run()

                self.assertEqual(result.completion_times, {"L1": 9})
                self.assertEqual(result.pm_count, 1)
                self.assertEqual(result.total_pm_downtime, 2)
                self.assertAlmostEqual(
                    sum(item.finish - item.start for item in result.processing_intervals),
                    5,
                )
                self.assertAlmostEqual(
                    sum(item.finish - item.start for item in result.load_intervals),
                    1,
                )
                self.assertAlmostEqual(
                    sum(item.finish - item.start for item in result.unload_intervals),
                    1,
                )

    def test_two_calendar_pms_together_can_interrupt_load_and_unload(self) -> None:
        """两个同机 Calendar PM 均生效，并分别续作 LOAD 与 UNLOAD。"""
        scenario = _scenario(
            calendar=(
                _scripted_pm("PM-LOAD", (0.5, 1)),
                _scripted_pm("PM-UNLOAD", (7.5, 1)),
            ),
            operation_duration=5,
            load=1,
            unload=1,
        )
        result = Simulator(scenario, seed=15).run()

        self.assertEqual(result.completion_times, {"L1": 9})
        self.assertEqual(result.pm_count, 2)
        self.assertEqual(
            [(item.pm_id, item.start, item.finish) for item in result.pm_intervals],
            [("PM-LOAD", 0.5, 1.5), ("PM-UNLOAD", 7.5, 8.5)],
        )
        self.assertAlmostEqual(
            sum(item.finish - item.start for item in result.load_intervals),
            1,
        )
        self.assertAlmostEqual(
            sum(item.finish - item.start for item in result.processing_intervals),
            5,
        )
        self.assertAlmostEqual(
            sum(item.finish - item.start for item in result.unload_intervals),
            1,
        )
        self.assertEqual(
            [(record.event_type, record.sim_time, record.pm_id)
             for record in result.trace
             if record.event_type in {"LOAD_SUSPEND", "LOAD_RESUME", "UNLOAD_SUSPEND", "UNLOAD_RESUME"}],
            [
                ("LOAD_SUSPEND", 0.5, "PM-LOAD"),
                ("LOAD_RESUME", 1.5, "PM-LOAD"),
                ("UNLOAD_SUSPEND", 7.5, "PM-UNLOAD"),
                ("UNLOAD_RESUME", 8.5, "PM-UNLOAD"),
            ],
        )

    def test_fixed_horizon_pm_intervals_and_statistics_conserve_observed_time(self) -> None:
        """fixed horizon 下已完成 PM 区间与活动 PM 尾段守恒。"""
        scenario = _scenario(
            calendar=(_scripted_pm("PM-A", (2, 1), (4, 10)),),
            operation_duration=20,
            horizon=5,
        )
        result = Simulator(scenario, seed=14).run()
        stats = result.machine_statistics["M1"]

        completed_pm_time = sum(
            item.finish - item.start for item in result.pm_intervals
        )
        active_pm_start = next(
            record.sim_time
            for record in result.trace
            if record.event_type == "PM_START" and record.pm_id == "PM-A"
            and record.pm_occurrence_index == 1
        )
        active_pm_time = scenario.horizon - active_pm_start
        self.assertEqual(
            [(item.start, item.finish) for item in result.pm_intervals],
            [(2, 3)],
        )
        self.assertAlmostEqual(
            completed_pm_time + active_pm_time,
            stats.pm_downtime,
        )
        self.assertAlmostEqual(stats.pm_downtime, 2)
        self.assertEqual(result.metrics.end_time, 5)
        self.assertEqual(result.metrics.completed_lots, 0)

    def test_pm_random_stream_is_independent_by_pm_id(self) -> None:
        """增加其他 PM 不得改变目标 pm_id 的 interval/duration 样本。"""
        target = _periodic_pm(
            "PM-TARGET",
            first_start=2,
            interval=TimeDistributionSpec("uniform", 5, 2),
            duration=TimeDistributionSpec("uniform", 2, 1),
            machine="M1",
        )
        extra = _periodic_pm(
            "PM-EXTRA",
            first_start=1,
            interval=TimeDistributionSpec("uniform", 3, 1),
            duration=TimeDistributionSpec("uniform", 1, 0.5),
            machine="M1",
        )

        isolated_streams = EntityRandomStreams(42)
        isolated_extra_streams = EntityRandomStreams(42)
        combined_streams = EntityRandomStreams(42)
        isolated_runtime = PMRuntime(
            (target,), (), isolated_streams
        )
        isolated_extra_runtime = PMRuntime(
            (extra,), (), isolated_extra_streams
        )
        combined_runtime = PMRuntime(
            (extra, target), (), combined_streams
        )
        isolated_first = isolated_runtime.initial_calendar_occurrences()[0]
        isolated_second = isolated_runtime.next_calendar_occurrence(isolated_first)
        combined_first = next(
            item
            for item in combined_runtime.initial_calendar_occurrences()
            if item.pm_id == "PM-TARGET"
        )
        combined_second = combined_runtime.next_calendar_occurrence(combined_first)
        combined_extra_first = next(
            item
            for item in combined_runtime.initial_calendar_occurrences()
            if item.pm_id == "PM-EXTRA"
        )
        combined_extra_second = combined_runtime.next_calendar_occurrence(
            combined_extra_first
        )
        isolated_extra_first = isolated_extra_runtime.initial_calendar_occurrences()[0]
        isolated_extra_second = isolated_extra_runtime.next_calendar_occurrence(
            isolated_extra_first
        )

        self.assertEqual(
            (isolated_first, isolated_second),
            (combined_first, combined_second),
        )
        self.assertEqual(
            {
                item.identity: item.value for item in isolated_streams.ledger
                if item.entity_id == "PM-TARGET"
            },
            {
                item.identity: item.value for item in combined_streams.ledger
                if item.entity_id == "PM-TARGET"
            },
        )
        self.assertEqual(
            {
                item.identity
                for item in isolated_streams.ledger
                if item.entity_id == "PM-TARGET"
            },
            {
                ("pm_duration", "PM-TARGET", 0),
                ("pm_interval", "PM-TARGET", 1),
                ("pm_duration", "PM-TARGET", 1),
            },
        )
        self.assertEqual(
            (isolated_extra_first, isolated_extra_second),
            (combined_extra_first, combined_extra_second),
        )
        self.assertEqual(
            {
                item.identity
                for item in isolated_extra_streams.ledger
                if item.entity_id == "PM-EXTRA"
            },
            {
                ("pm_duration", "PM-EXTRA", 0),
                ("pm_interval", "PM-EXTRA", 1),
                ("pm_duration", "PM-EXTRA", 1),
            },
        )
        self.assertEqual(
            {
                item.identity: item.value
                for item in isolated_extra_streams.ledger
                if item.entity_id == "PM-EXTRA"
            },
            {
                item.identity: item.value
                for item in combined_streams.ledger
                if item.entity_id == "PM-EXTRA"
            },
            {
                ("pm_duration", "PM-EXTRA", 0),
                ("pm_interval", "PM-EXTRA", 1),
                ("pm_duration", "PM-EXTRA", 1),
            },
        )

    def test_fixed_horizon_boundary_same_time_calendar_pms_have_one_owner(self) -> None:
        """恰在 H 到达的同刻 PM 仍执行，且只产生一个停机 owner。"""
        horizon = 5
        result = Simulator(
            _scenario(
                calendar=(
                    _scripted_pm("PM-A", (horizon, 2)),
                    _scripted_pm("PM-B", (horizon, 1)),
                ),
                horizon=horizon,
            ),
            seed=16,
        ).run()

        records = _same_time_pm_records(result, horizon)
        self.assertEqual(
            [(record.event_type, record.pm_id, record.state_after) for record in records],
            [
                ("PM_START", "PM-A", "AVAILABILITY:DOWN"),
                ("PM_START_STALE", "PM-B", "NO_EFFECT"),
            ],
        )
        self.assertLess(records[0].cause_event_seq, records[1].cause_event_seq)
        self.assertEqual(result.pm_count, 1)
        self.assertEqual(result.pm_intervals, ())
        self.assertEqual(result.metrics.end_time, horizon)
        self.assertEqual(result.machine_statistics["M1"].pm_downtime, 0)

    def test_multiple_wafer_pm_specs_on_one_machine_remain_explicitly_rejected(self) -> None:
        """当前 contract 仍只允许每台 machine 一条 wafer PM。"""
        wafer_a = WaferPMSpec(
            "PM-WA-A",
            "M1",
            25,
            TimeDistributionSpec("constant", 1),
        )
        wafer_b = WaferPMSpec(
            "PM-WA-B",
            "M1",
            50,
            TimeDistributionSpec("constant", 1),
        )
        with self.assertRaisesRegex(ValueError, "最多一条 wafer PM"):
            _scenario(wafer_pm=(wafer_a, wafer_b))


if __name__ == "__main__":
    unittest.main()
