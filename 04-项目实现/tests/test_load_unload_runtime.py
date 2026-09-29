"""Non-cascade load/process/unload synthetic acceptance tests.

These tests exercise the public ``MachineSpec`` load/unload fields and the
``SimulationResult`` audit surface.  ``PROCESS_FINISH`` remains the canonical
business completion boundary after unloading.
"""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path
import sys
import unittest

SOURCE_ROOT = Path(__file__).resolve().parents[1] / "src"
sys.path.insert(0, str(SOURCE_ROOT))

from fab_scheduler.domain.models import (  # noqa: E402
    BatchSpec,
    CalendarPMSpec,
    CQTSpec,
    LotSpec,
    MachineFailureSpec,
    MachineSpec,
    OperationSpec,
    Scenario,
    ScriptedFailureSpec,
    ScriptedPMSpec,
    TimeDistributionSpec,
)
from fab_scheduler.simulation.engine import SimulationError, Simulator  # noqa: E402
from fab_scheduler.evaluation.audit import audit_result_invariants  # noqa: E402


class LoadUnloadRuntimeTests(unittest.TestCase):
    """Acceptance boundaries for the limited non-cascade L/U chain."""

    @staticmethod
    def _scenario(
        *,
        lots: tuple[LotSpec, ...] = (),
        load: float = 1,
        unload: float = 1,
        horizon: float | None = None,
        processing_distribution: TimeDistributionSpec | None = None,
        failure_specs: tuple[MachineFailureSpec, ...] = (),
        calendar_pm_specs: tuple[CalendarPMSpec, ...] = (),
        batch_spec: BatchSpec | None = None,
        cascading: bool = False,
    ) -> Scenario:
        if not lots:
            operation = OperationSpec(
                step_id=1,
                processing_time=5,
                eligible_machines=("M1",),
                route_id="SYNTH-LU",
                processing_distribution=processing_distribution,
                batch_spec=batch_spec,
            )
            lots = (
                LotSpec(
                    lot_id="L1",
                    release_time=0,
                    operations=(operation,),
                ),
            )
        return Scenario(
            scenario_id="SYNTH_NON_CASCADE_LU",
            dataset_version="synthetic@load-unload-0.1.0",
            machines=(
                MachineSpec(
                    "M1",
                    load_minutes=load,
                    unload_minutes=unload,
                    cascading=cascading,
                ),
            ),
            lots=lots,
            termination_mode=(
                "fixed_horizon" if horizon is not None else "until_all_complete"
            ),
            horizon=horizon,
            failure_specs=failure_specs,
            calendar_pm_specs=calendar_pm_specs,
        )

    @staticmethod
    def _one_lot_operation(
        *,
        lot_id: str = "L1",
        release_time: float = 0,
        processing_distribution: TimeDistributionSpec | None = None,
        batch_spec: BatchSpec | None = None,
    ) -> LotSpec:
        return LotSpec(
            lot_id=lot_id,
            release_time=release_time,
            operations=(
                OperationSpec(
                    step_id=1,
                    processing_time=5,
                    eligible_machines=("M1",),
                    route_id="SYNTH-LU",
                    processing_distribution=processing_distribution,
                    batch_spec=batch_spec,
                ),
            ),
        )

    @staticmethod
    def _events(result, event_type: str, lot_id: str | None = None):
        return [
            record
            for record in result.trace
            if record.event_type == event_type
            and (lot_id is None or record.lot_id == lot_id)
        ]

    def test_positive_load_unload_finish_is_after_both_and_keeps_machine_busy(self) -> None:
        """load=1 + process=5 + unload=1 has canonical finish at t=7."""
        result = Simulator(self._scenario()).run()

        finish = self._events(result, "PROCESS_FINISH", "L1")
        self.assertEqual(len(finish), 1)
        self.assertEqual(finish[0].sim_time, 7)
        self.assertEqual(result.completion_times, {"L1": 7})
        self.assertEqual(result.metrics.completed_lots, 1)
        self.assertEqual(result.metrics.terminal_wip_lots, 0)

        # PROCESS intervals account for core processing only; L/U must not be
        # silently folded into the processing sample or processing metric.
        self.assertEqual(len(result.processing_intervals), 1)
        interval = result.processing_intervals[0]
        self.assertEqual((interval.start, interval.finish), (1, 6))
        self.assertEqual(
            sum(item.finish - item.start for item in result.processing_intervals),
            5,
        )
        self.assertEqual(
            [(item.event_type, item.sim_time) for item in result.trace if item.event_type in {
                "LOAD_START", "LOAD_FINISH", "PROCESS_START", "PROCESS_CORE_FINISH",
                "UNLOAD_START", "UNLOAD_FINISH", "PROCESS_FINISH",
            }],
            [("LOAD_START", 0), ("LOAD_FINISH", 1), ("PROCESS_START", 1),
             ("PROCESS_CORE_FINISH", 6), ("UNLOAD_START", 6),
             ("UNLOAD_FINISH", 7), ("PROCESS_FINISH", 7)],
        )
        self.assertTrue(audit_result_invariants(result, self._scenario()).passed)

    def test_machine_cannot_dispatch_second_lot_before_first_unload_finishes(self) -> None:
        lots = (
            self._one_lot_operation(lot_id="L1"),
            self._one_lot_operation(lot_id="L2"),
        )
        result = Simulator(self._scenario(lots=lots)).run()

        finishes = self._events(result, "PROCESS_FINISH")
        self.assertEqual(
            [(item.lot_id, item.sim_time) for item in finishes],
            [("L1", 7), ("L2", 14)],
        )
        second_dispatch = self._events(result, "DISPATCH", "L2")
        self.assertEqual(len(second_dispatch), 1)
        self.assertGreaterEqual(second_dispatch[0].sim_time, 7)
        # Depending on whether the runtime exposes the core phase explicitly,
        # the canonical start is named PROCESS_START or PROCESS_CORE_START.
        second_process = [
            record
            for record in result.trace
            if record.lot_id == "L2"
            and record.event_type in {"PROCESS_START", "PROCESS_CORE_START"}
        ]
        self.assertEqual(len(second_process), 1)
        self.assertGreaterEqual(second_process[0].sim_time, 8)

        # Each lot has one canonical finish and one completion, and process
        # intervals do not overlap on the single physical machine.
        self.assertEqual(
            [item.lot_id for item in self._events(result, "LOT_COMPLETE")],
            ["L1", "L2"],
        )
        intervals = sorted(result.processing_intervals, key=lambda item: item.start)
        self.assertEqual([(item.start, item.finish) for item in intervals], [(1, 6), (8, 13)])
        self.assertLessEqual(intervals[0].finish, intervals[1].start)

    def test_processing_sample_is_realized_once_at_core_start_and_resume_does_not_resample(self) -> None:
        # Uniform width zero is deterministic at 5 minutes but still leaves a
        # ledger entry, allowing this test to audit the one-sample contract.
        distribution = TimeDistributionSpec("uniform", 5, 0)
        result = Simulator(
            self._scenario(
                processing_distribution=distribution,
                failure_specs=(
                    MachineFailureSpec(
                        "M1",
                        "scripted",
                        scripted_failures=(ScriptedFailureSpec(3, 2),),
                    ),
                ),
            ),
            seed=17,
        ).run()

        samples = [
            item
            for item in result.random_sample_ledger
            if item.stream_name == "processing"
        ]
        self.assertEqual(len(samples), 1)
        self.assertEqual(samples[0].value, 5)
        self.assertEqual(result.completion_times["L1"], 9)
        self.assertEqual(
            sum(item.finish - item.start for item in result.processing_intervals),
            5,
        )

    def test_failure_and_calendar_pm_resume_remaining_time_for_each_stage(self) -> None:
        """A downtime in LOAD, PROCESS, or UNLOAD adds only its duration."""
        stage_starts = {
            "LOAD": 0.5,
            "PROCESS": 3,
            "UNLOAD": 6.5,
        }
        for downtime_kind in ("failure", "calendar_pm"):
            for stage, start in stage_starts.items():
                with self.subTest(downtime_kind=downtime_kind, stage=stage):
                    if downtime_kind == "failure":
                        scenario = self._scenario(
                            failure_specs=(
                                MachineFailureSpec(
                                    "M1",
                                    "scripted",
                                    scripted_failures=(
                                        ScriptedFailureSpec(start, 2),
                                    ),
                                ),
                            ),
                        )
                    else:
                        scenario = self._scenario(
                            calendar_pm_specs=(
                                CalendarPMSpec(
                                    "PM-M1",
                                    "M1",
                                    "scripted",
                                    scripted_occurrences=(
                                        ScriptedPMSpec(start, 2),
                                    ),
                                ),
                            ),
                        )
                    result = Simulator(scenario, seed=23).run()
                    self.assertEqual(result.completion_times["L1"], 9)
                    self.assertEqual(result.metrics.completed_lots, 1)
                    self.assertEqual(
                        sum(
                            item.finish - item.start
                            for item in result.processing_intervals
                        ),
                        5,
                    )

    def test_fixed_horizon_mid_unload_keeps_lot_in_terminal_wip(self) -> None:
        result = Simulator(self._scenario(horizon=6)).run()

        self.assertEqual(result.metrics.end_time, 6)
        self.assertEqual(result.metrics.released_lots, 1)
        self.assertEqual(result.metrics.completed_lots, 0)
        self.assertEqual(result.metrics.terminal_wip_lots, 1)
        self.assertNotIn("PROCESS_FINISH", {item.event_type for item in result.trace})
        self.assertNotIn("LOT_COMPLETE", {item.event_type for item in result.trace})

    def test_fixed_horizon_snapshots_each_phase_and_conserves_time(self) -> None:
        for horizon, expected_load, expected_process, expected_unload in (
            (0.5, 0.5, 0, 0),
            (3, 1, 2, 0),
            (6.5, 1, 5, 0.5),
        ):
            with self.subTest(horizon=horizon):
                scenario = self._scenario(horizon=horizon)
                result = Simulator(scenario).run()
                stats = result.machine_statistics["M1"]
                self.assertEqual(stats.load_time, expected_load)
                self.assertEqual(stats.processing_time, expected_process)
                self.assertEqual(stats.unload_time, expected_unload)
                self.assertEqual(result.metrics.terminal_wip_lots, 1)
                self.assertTrue(audit_result_invariants(result, scenario).passed)

    def test_cqt_closes_at_target_core_start_after_load(self) -> None:
        operations = (
            OperationSpec(1, 5, ("M1",), route_id="SYNTH-LU"),
            OperationSpec(2, 5, ("M1",), route_id="SYNTH-LU"),
        )
        scenario = Scenario(
            scenario_id="LU_CQT",
            dataset_version="synthetic@load-unload-0.1.0",
            machines=(MachineSpec("M1", load_minutes=1, unload_minutes=1),),
            lots=(LotSpec("L1", 0, operations),),
            cqt_constraints=(CQTSpec("CQT-1-2", "SYNTH-LU", 1, 2, 2),),
        )
        result = Simulator(scenario).run()
        self.assertEqual(result.completion_times["L1"], 14)
        self.assertEqual(len(result.cqt_records), 1)
        self.assertEqual(result.cqt_records[0].opened_at, 7)
        self.assertEqual(result.cqt_records[0].closed_at, 8)
        self.assertTrue(audit_result_invariants(result, scenario).passed)

    def test_unrelated_batch_machine_is_not_rejected(self) -> None:
        batch = BatchSpec(25, 25, 25, 0)
        scenario = Scenario(
            scenario_id="LU_AND_INDEPENDENT_BATCH",
            dataset_version="synthetic@load-unload-0.1.0",
            machines=(
                MachineSpec("M1", load_minutes=1, unload_minutes=1),
                MachineSpec("M2"),
            ),
            lots=(
                self._one_lot_operation(),
                LotSpec("L2", 0, (OperationSpec(1, 5, ("M2",), route_id="BATCH-R", batch_spec=batch),)),
            ),
        )
        result = Simulator(scenario).run()
        self.assertTrue(audit_result_invariants(result, scenario).passed)

    def test_zero_load_unload_preserves_legacy_processing_timeline(self) -> None:
        result = Simulator(self._scenario(load=0, unload=0)).run()

        finish = self._events(result, "PROCESS_FINISH", "L1")
        self.assertEqual(len(finish), 1)
        self.assertEqual(finish[0].sim_time, 5)
        self.assertEqual(result.completion_times, {"L1": 5})
        self.assertEqual(
            [(item.start, item.finish) for item in result.processing_intervals],
            [(0, 5)],
        )
        self.assertEqual(result.machine_statistics["M1"].processing_time, 5)

    def test_independent_audit_rejects_phase_time_and_provenance_tampering(self) -> None:
        scenario = self._scenario(horizon=10)
        result = Simulator(scenario).run()
        self.assertTrue(audit_result_invariants(result, scenario).passed)

        stats = result.machine_statistics["M1"]
        corrupted_stats = replace(
            result,
            machine_statistics={"M1": replace(stats, load_time=stats.load_time + 1)},
        )
        self.assertFalse(audit_result_invariants(corrupted_stats, scenario).passed)

        corrupted_intervals = replace(
            result,
            load_intervals=(replace(result.load_intervals[0], finish=6),),
        )
        self.assertFalse(audit_result_invariants(corrupted_intervals, scenario).passed)

        provenance = replace(
            result.provenance,
            simulation_config={
                **result.provenance.simulation_config,
                "load_unload_runtime": {"schema_version": "bad"},
            },
        )
        self.assertFalse(audit_result_invariants(replace(result, provenance=provenance), scenario).passed)

    def test_batch_with_load_unload_is_explicitly_rejected(self) -> None:
        batch = BatchSpec(25, 25, 25, 0)
        with self.assertRaises((ValueError, SimulationError)):
            scenario = self._scenario(batch_spec=batch)
            Simulator(scenario).run()

    def test_cascade_is_explicitly_rejected_by_non_cascade_runtime(self) -> None:
        with self.assertRaises((ValueError, SimulationError)):
            scenario = self._scenario(cascading=True)
            Simulator(scenario).run()

    def test_interval_is_rejected_even_without_positive_load_unload(self) -> None:
        for interval_field in ("part_interval_minutes", "batch_interval_minutes"):
            with self.subTest(interval_field=interval_field):
                operation = OperationSpec(
                    1,
                    5,
                    ("M1",),
                    route_id="SYNTH-LU",
                    **{interval_field: 1},
                )
                with self.assertRaisesRegex(ValueError, "双时点 cascade runtime 尚未实现"):
                    Scenario(
                        scenario_id="NO_SILENT_CASCADE_INTERVAL",
                        dataset_version="synthetic@load-unload-0.1.0",
                        machines=(MachineSpec("M1"),),
                        lots=(LotSpec("L1", 0, (operation,)),),
                    )


if __name__ == "__main__":
    unittest.main()
