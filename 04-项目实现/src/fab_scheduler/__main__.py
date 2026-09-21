"""命令行入口。当前仅提供项目状态信息，不执行仿真。"""

import argparse


def main() -> int:
    parser = argparse.ArgumentParser(
        prog="fab_scheduler",
        description="半导体制造系统智能调度课程设计脚手架",
    )
    subparsers = parser.add_subparsers(dest="command")
    subparsers.add_parser("info", help="显示项目阶段和实现状态")
    args = parser.parse_args()

    if args.command == "info":
        print("项目阶段：M1 Simulation Reliability Baseline")
        print("项目周期：14 周")
        print("仿真状态：可信轻量 DES 已验证 MC01-MC08；M1 已通过")
        print("数据状态：SMT2020 Data Integration Gate 待验证")
        print("优化器状态：CMA-ES 已选型，Data Integration Gate 通过前禁用")
        return 0

    parser.print_help()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
