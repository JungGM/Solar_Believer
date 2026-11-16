"""Explicit operator commands; no public mutation API in the initial scaffold."""

import argparse

from .config import get_settings
from .db import session_factory
from .jobs import JOB_TYPES, run_job
from .services import activate_model, approve_model


def main():
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command", required=True)
    job = sub.add_parser("run")
    job.add_argument("job_type", choices=JOB_TYPES)
    for command in ("approve-model", "activate-model"):
        model = sub.add_parser(command)
        model.add_argument("version")
        if command == "approve-model":
            model.add_argument("--approver", required=True)
    args = parser.parse_args()
    if args.command == "run":
        status = run_job(args.job_type)
        print(status)
        return 1 if status == "failed" else 0
    with session_factory().begin() as session:
        artifact_dir = get_settings().model_artifact_dir
        if args.command == "approve-model":
            approve_model(session, args.version, args.approver, artifact_dir)
        else:
            activate_model(session, args.version, artifact_dir)
    print("completed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
