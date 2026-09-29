"""证据受限 release template runtime 的纯函数。"""

from __future__ import annotations

from decimal import Decimal

from fab_scheduler.domain.models import LotSpec, ReleaseTemplateSpec


RELEASE_RUNTIME_SCHEMA_VERSION = "0.1.0"
RELEASE_RUNTIME_ID = "fixed_horizon_constant_interval_lots_per_repeat_1"


def release_lot_id(
    template_id: str,
    lot_prefix: str,
    repeat_index: int,
    member_index: int,
) -> str:
    """返回冻结的 release lot 身份格式。"""

    return (
        f"REL::{template_id}::{lot_prefix}::"
        f"r{repeat_index:06d}::m{member_index:04d}"
    )


def release_time(template: ReleaseTemplateSpec, repeat_index: int) -> float:
    """constant interval 下第 ``repeat_index`` 次 occurrence 的时刻。"""

    # SMT2020 的时间字段来自十进制文本。先按输入 float 的十进制表示组合，
    # 再一次性转回 float，避免 3 * 0.1 被算成 0.30000000000000004，
    # 从而错误排除数学上恰好位于 fixed horizon 的 release。
    return float(
        Decimal(str(template.first_release_time))
        + Decimal(repeat_index) * Decimal(str(template.interval.mean_minutes))
    )


def materialize_release_lot(
    template: ReleaseTemplateSpec,
    *,
    repeat_index: int,
    member_index: int = 0,
) -> LotSpec:
    """把一个 occurrence 转成普通 LotSpec；不包含任何首段 transport。"""

    if not 0 <= repeat_index < template.repeat_limit:
        raise ValueError("release repeat_index 超出 template repeat_limit")
    if member_index != 0:
        raise ValueError("当前 release runtime 仅支持 member_index=0")
    when = release_time(template, repeat_index)
    due_time = (
        when + template.relative_due_minutes
        if template.relative_due_minutes is not None
        else None
    )
    return LotSpec(
        lot_id=release_lot_id(
            template.template_id,
            template.lot_prefix,
            repeat_index,
            member_index,
        ),
        release_time=when,
        operations=template.operations,
        quantity_wafers=template.quantity_wafers,
        due_time=due_time,
        priority=template.priority,
        product_id=template.product_id,
        order_id=template.order_id,
        hot_lot=template.hot_lot,
        release_template_id=template.template_id,
        release_repeat_index=repeat_index,
        release_member_index=member_index,
        source_row=template.source_row,
    )
