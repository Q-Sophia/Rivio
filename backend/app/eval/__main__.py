from __future__ import annotations

import argparse
import json
from pathlib import Path

from app.eval.runner import DEFAULT_WORKSPACE_ROOT, EvaluationRunner


def _add_execution_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--case", default="", help="只运行一个 case，例如 case_01")
    parser.add_argument("--limit", type=int, default=None, help="只运行前 N 个 case")
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=DEFAULT_WORKSPACE_ROOT,
        help="Evaluation Workspace 根目录",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="覆盖 frozen input 或当前 run 中已存在的 variant 输出",
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m app.eval",
        description="RIVIO-EVAL-R1 Research A/B Evaluation Runner",
    )
    commands = parser.add_subparsers(dest="command", required=True)
    for name in ("prepare", "e2", "e3", "all"):
        _add_execution_args(commands.add_parser(name))
    summary = commands.add_parser("summary")
    summary.add_argument(
        "--output-dir",
        type=Path,
        default=DEFAULT_WORKSPACE_ROOT,
        help="Evaluation Workspace 根目录",
    )
    return parser


def main() -> None:
    args = build_parser().parse_args()
    runner = EvaluationRunner(workspace_root=args.output_dir)
    if args.command == "prepare":
        result = runner.prepare(
            case_id=args.case,
            limit=args.limit,
            force=args.force,
        )
    elif args.command == "e2":
        result = {
            "run_dir": str(
                runner.run_e2(
                    case_id=args.case,
                    limit=args.limit,
                    force=args.force,
                ).resolve()
            )
        }
    elif args.command == "e3":
        result = {
            "run_dir": str(
                runner.run_e3(
                    case_id=args.case,
                    limit=args.limit,
                    force=args.force,
                ).resolve()
            )
        }
    elif args.command == "all":
        result = {
            "run_dir": str(
                runner.run_all(
                    case_id=args.case,
                    limit=args.limit,
                    force=args.force,
                ).resolve()
            )
        }
    else:
        result = runner.summary()
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
