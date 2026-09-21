"""M1-E06：四种 baseline 共享 Engine action pipeline。"""

from __future__ import annotations

from dataclasses import dataclass, field
import math
import unittest

from fab_scheduler.domain.models import (
    BatchSpec,
    CalendarPMSpec,
    DedicationSpec,
    LotSpec,
    MachineFailureSpec,
    MachineSpec,
    OperationSpec,
    Scenario,
    ScriptedFailureSpec,
    ScriptedPMSpec,
)
from fab_scheduler.policies.base import (
    DispatchAction,
    DispatchContext,
    DispatchPolicy,
)
from fab_scheduler.policies.cr import CRPolicy
from fab_scheduler.policies.edd import EDDPolicy
from fab_scheduler.policies.fifo import FIFOPolicy
from fab_scheduler.policies.spt import SPTPolicy
from fab_scheduler.simulation.engine import Simulator


def action(
    action_id: str,
    lot_ids: tuple[str, ...],
    *,
    processing: float,
    due: tuple[float | None, ...],
    remaining: tuple[float, ...],
    queue_time: float = 0,
    action_type: str = "ordinary",
) -> DispatchAction:
    return DispatchAction(
        action_id=action_id,
        lot_id=lot_ids[0],
        machine_id="M1",
        action_type=action_type,
        member_lot_ids=lot_ids,
        operation_index=0,
        route_id="R",
        step_id=1,
        queue_entered_at=queue_time,
        release_time=0,
        physical_processing_time=processing,
        member_due_times=due,
        member_remaining_nominal_processing_times=remaining,
    )


@dataclass
class RecordingPolicy:
    delegate: DispatchPolicy
    calls: list[tuple[tuple[object, ...], ...]] = field(default_factory=list)

    @property
    def name(self) -> str:
        return self.delegate.name

    @property
    def policy_id(self) -> str:
        return self.delegate.policy_id

    def select(
        self,
        state: DispatchContext,
        feasible_actions: tuple[DispatchAction, ...] | list[DispatchAction],
    ) -> DispatchAction | None:
        self.calls.append(
            tuple(item.canonical_snapshot() for item in feasible_actions)
        )
        return self.delegate.select(state, feasible_actions)


def operation(
    duration: float,
    machines: tuple[str, ...] = ("M1",),
    **kwargs: object,
) -> OperationSpec:
    return OperationSpec(1, duration, machines, route_id="R", **kwargs)


class DispatchPolicyDefinitionTests(unittest.TestCase):
    def test_spt_golden_and_setup_is_not_added(self) -> None:
        long = action("A-L1", ("L1",), processing=10, due=(None,), remaining=(10,))
        short = action("A-L2", ("L2",), processing=2, due=(None,), remaining=(2,))
        selected = SPTPolicy().select(DispatchContext(0), (long, short))
        self.assertIs(selected, short)

    def test_edd_golden_and_missing_due_is_infinity(self) -> None:
        missing = action("A-L1", ("L1",), processing=1, due=(None,), remaining=(1,))
        due_20 = action("A-L2", ("L2",), processing=10, due=(20,), remaining=(10,))
        selected = EDDPolicy().select(DispatchContext(0), (missing, due_20))
        self.assertIs(selected, due_20)
        self.assertTrue(math.isinf(missing.representative_due_date))

    def test_cr_golden_formula_and_negative_ratio(self) -> None:
        l1 = action("A-L1", ("L1",), processing=10, due=(30,), remaining=(10,))
        l2 = action("A-L2", ("L2",), processing=5, due=(25,), remaining=(30,))
        overdue = action("A-L3", ("L3",), processing=2, due=(5,), remaining=(5,))
        context = DispatchContext(10)
        self.assertEqual(l1.critical_ratio(context.current_time), 2)
        self.assertEqual(l2.critical_ratio(context.current_time), 0.5)
        self.assertEqual(overdue.critical_ratio(context.current_time), -1)
        self.assertIs(CRPolicy().select(context, (l1, l2)), l2)

    def test_batch_uses_one_physical_duration_min_due_and_min_cr(self) -> None:
        batch = action(
            "B-M1-L1-L2",
            ("L1", "L2"),
            processing=4,
            due=(100, 20),
            remaining=(40, 10),
            action_type="batch",
        )
        ordinary = action(
            "A-L3", ("L3",), processing=5, due=(30,), remaining=(5,)
        )
        self.assertIs(SPTPolicy().select(DispatchContext(0), (ordinary, batch)), batch)
        self.assertIs(EDDPolicy().select(DispatchContext(0), (ordinary, batch)), batch)
        self.assertEqual(batch.critical_ratio(10), min(90 / 40, 10 / 10))
        self.assertEqual(batch.member_lot_ids, ("L1", "L2"))

    def test_all_policies_use_same_deterministic_tie_break(self) -> None:
        z = action("Z", ("L1",), processing=2, due=(20,), remaining=(10,))
        a = action("A", ("L2",), processing=2, due=(20,), remaining=(10,))
        for policy in (FIFOPolicy(), SPTPolicy(), EDDPolicy(), CRPolicy()):
            with self.subTest(policy=policy.name):
                if isinstance(policy, FIFOPolicy):
                    # FIFO 在 primary key 相等后保留既有 lot_id 平局规则。
                    self.assertIs(policy.select(DispatchContext(0), (z, a)), z)
                else:
                    self.assertIs(policy.select(DispatchContext(0), (z, a)), a)


class SharedFeasibleActionPipelineTests(unittest.TestCase):
    POLICIES = (FIFOPolicy, SPTPolicy, EDDPolicy, CRPolicy)

    def test_four_policies_receive_identical_initial_action_set(self) -> None:
        scenario = Scenario(
            "COMMON_ACTION_SET",
            "micro@policy-0.1.0",
            (MachineSpec("M1"),),
            (
                LotSpec("L1", 0, (operation(10),), due_time=100),
                LotSpec("L2", 0, (operation(2),), due_time=5),
            ),
        )
        snapshots = []
        starts: dict[str, str] = {}
        for policy_type in self.POLICIES:
            recorder = RecordingPolicy(policy_type())
            result = Simulator(scenario, policy=recorder).run()
            snapshots.append(recorder.calls[0])
            starts[recorder.name] = next(
                row["lot"]
                for row in result.key_trace()
                if row["event"] == "PROCESS_START"
            )
        self.assertTrue(all(value == snapshots[0] for value in snapshots[1:]))
        self.assertEqual(starts["FIFO"], "L1")
        self.assertEqual(starts["SPT"], "L2")
        self.assertEqual(starts["EDD"], "L2")
        self.assertEqual(starts["CR"], "L2")

    def test_engine_hides_qualification_reserved_and_illegal_batch_actions(self) -> None:
        batch_spec = BatchSpec(125, 150, 150, 20)
        scenario = Scenario(
            "HARD_FILTERS",
            "micro@policy-0.1.0",
            (MachineSpec("M1"), MachineSpec("M2")),
            (
                LotSpec("L-RESERVE", 0, (operation(5, ("M1", "M2")),)),
                LotSpec("L-M2-ONLY", 0, (operation(3, ("M2",)),)),
                *tuple(
                    LotSpec(
                        f"B{i}",
                        0,
                        (operation(4, ("M1",), batch_spec=batch_spec),),
                        quantity_wafers=25,
                    )
                    for i in range(1, 5)
                ),
            ),
            termination_mode="fixed_horizon",
            horizon=1,
        )
        for policy_type in self.POLICIES:
            recorder = RecordingPolicy(policy_type())
            Simulator(scenario, policy=recorder).run()
            flattened = [item for call in recorder.calls for item in call]
            m1 = [item for item in flattened if item[1] == "M1"]
            m2 = [item for item in flattened if item[1] == "M2"]
            self.assertTrue(all("L-M2-ONLY" not in item[3] for item in m1))
            self.assertTrue(all(not item[0].startswith("BATCH|") for item in m1))
            self.assertTrue(all("L-RESERVE" not in item[3] for item in m2))

    def test_down_machine_never_calls_policy(self) -> None:
        scenario = Scenario(
            "DOWN_FILTER",
            "micro@policy-0.1.0",
            (MachineSpec("M1"),),
            (LotSpec("L1", 0, (operation(1),)),),
            termination_mode="fixed_horizon",
            horizon=1,
            failure_specs=(
                MachineFailureSpec(
                    "M1", "scripted", (ScriptedFailureSpec(0, 5),)
                ),
            ),
        )
        for policy_type in self.POLICIES:
            recorder = RecordingPolicy(policy_type())
            Simulator(scenario, policy=recorder).run()
            self.assertEqual(recorder.calls, [])

    def test_pm_active_machine_never_calls_policy(self) -> None:
        scenario = Scenario(
            "PM_FILTER",
            "micro@policy-0.1.0",
            (MachineSpec("M1"),),
            (LotSpec("L1", 0, (operation(1),)),),
            termination_mode="fixed_horizon",
            horizon=1,
            calendar_pm_specs=(
                CalendarPMSpec(
                    "PM1", "M1", "scripted", (ScriptedPMSpec(0, 5),)
                ),
            ),
        )
        for policy_type in self.POLICIES:
            recorder = RecordingPolicy(policy_type())
            Simulator(scenario, policy=recorder).run()
            self.assertEqual(recorder.calls, [])

    def test_dedicated_lot_is_hidden_from_other_qualified_machine(self) -> None:
        route = "DED"
        scenario = Scenario(
            "DEDICATION_FILTER",
            "micro@policy-0.1.0",
            (MachineSpec("M1"), MachineSpec("M2"), MachineSpec("M3")),
            (
                LotSpec(
                    "L1",
                    0,
                    (
                        OperationSpec(1, 1, ("M1",), route_id=route),
                        OperationSpec(2, 1, ("M3",), route_id=route),
                        OperationSpec(3, 1, ("M1", "M2"), route_id=route),
                    ),
                ),
                LotSpec(
                    "BLOCKER",
                    1,
                    (OperationSpec(1, 10, ("M1",), route_id="AUX"),),
                ),
            ),
            dedication_constraints=(DedicationSpec("D1", route, 1, 3),),
        )
        for policy_type in self.POLICIES:
            recorder = RecordingPolicy(policy_type())
            result = Simulator(scenario, policy=recorder).run()
            seen_on_m2 = [
                snapshot
                for call in recorder.calls
                for snapshot in call
                if snapshot[1] == "M2" and "L1" in snapshot[3]
            ]
            self.assertEqual(seen_on_m2, [])
            target = [
                row
                for row in result.key_trace()
                if row["event"] == "PROCESS_START"
                and row.get("lot") == "L1"
                and row.get("step") == 3
            ]
            self.assertEqual(target[0]["machine"], "M1")

    def test_engine_emits_atomic_batch_action_without_reformation(self) -> None:
        batch_spec = BatchSpec(125, 150, 125, 0)
        scenario = Scenario(
            "BATCH_ACTION",
            "micro@policy-0.1.0",
            (MachineSpec("M1"),),
            tuple(
                LotSpec(
                    f"L{i}",
                    0,
                    (operation(4, batch_spec=batch_spec),),
                    quantity_wafers=25,
                    due_time=100 - i,
                )
                for i in range(1, 6)
            ),
        )
        recorder = RecordingPolicy(SPTPolicy())
        result = Simulator(scenario, policy=recorder).run()
        first = recorder.calls[0][0]
        self.assertEqual(first[2], "batch")
        self.assertEqual(first[3], ("L1", "L2", "L3", "L4", "L5"))
        self.assertEqual(first[8], 4)
        self.assertEqual(len(result.batch_intervals), 1)


if __name__ == "__main__":
    unittest.main()
