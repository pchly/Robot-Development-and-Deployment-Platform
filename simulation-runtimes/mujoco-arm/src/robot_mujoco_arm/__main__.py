"""Command line entry point for the MuJoCo virtual arm."""

from __future__ import annotations

import argparse
import json

from robot_mujoco_arm.runtime import CartesianTarget, MujocoArmRuntime


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="运行MuJoCo六轴虚拟机械臂")
    parser.add_argument("--target", nargs=3, type=float, metavar=("X", "Y", "Z"), required=True)
    parser.add_argument("--steps", type=int, default=1000)
    parser.add_argument("--headless", action="store_true")
    return parser


def main() -> int:
    arguments = build_parser().parse_args()
    runtime = MujocoArmRuntime()
    runtime.enable_commands()
    target = CartesianTarget(*arguments.target)
    state = runtime.command_target(target, simulation_steps=arguments.steps)
    print(json.dumps(state.to_dict(), ensure_ascii=False))

    if not arguments.headless:
        import mujoco.viewer

        mujoco.viewer.launch(runtime.model, runtime.data)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
