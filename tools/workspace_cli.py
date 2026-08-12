"""Workspace identity and coordinated-migration command line."""

from __future__ import annotations

import argparse
import getpass
import os
import sys
from pathlib import Path

from identity.bootstrap import DEFAULT_MANIFEST_PATH, BootstrapError, bootstrap_owner
from identity.primitives import normalize_email
from tools.staging_migrations import (
    StagingMigrationError,
    prepare_cutover,
    stage_domains,
    verify_staging,
)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m tools.workspace_cli")
    commands = parser.add_subparsers(dest="command", required=True)
    bootstrap = commands.add_parser("bootstrap-owner")
    bootstrap.add_argument("--email", required=True)
    for name in ("stage-domains", "verify-staging", "prepare-cutover"):
        command = commands.add_parser(name)
        command.add_argument("--manifest", type=Path, default=Path(os.environ.get("WORKSPACE_MIGRATION_MANIFEST_PATH", DEFAULT_MANIFEST_PATH)))
    return parser


def _valid_email(value: str) -> bool:
    local, separator, domain = value.partition("@")
    return bool(
        separator
        and local
        and domain
        and "@" not in domain
        and not any(character.isspace() for character in value)
    )


def _contains_forbidden_password_option(argv: list[str]) -> bool:
    return any(
        argument == "--password" or argument.startswith("--password=")
        for argument in argv
    )


def main(argv: list[str] | None = None) -> int:
    arguments = list(sys.argv[1:] if argv is None else argv)
    if _contains_forbidden_password_option(arguments):
        print(
            "bootstrap failed: password command arguments are forbidden",
            file=sys.stderr,
        )
        return 2
    args = _parser().parse_args(arguments)
    if args.command in {"stage-domains", "verify-staging", "prepare-cutover"}:
        try:
            if args.command == "stage-domains":
                result = stage_domains(args.manifest)
                print(f"release_id={result['release_id']}")
                print("staging=verified")
            elif args.command == "verify-staging":
                result = verify_staging(args.manifest)
                print(f"release_id={result['release_id']}")
                print("verification=passed")
            else:
                cutover_path, rollback_path = prepare_cutover(args.manifest)
                print(f"cutover_plan={cutover_path}")
                print(f"rollback_manifest={rollback_path}")
                print("production_promotion_allowed=false")
        except (StagingMigrationError, OSError, ValueError) as error:
            print(f"{args.command} failed: {error}", file=sys.stderr)
            return 1
        return 0
    if args.command != "bootstrap-owner": raise AssertionError("unknown command")
    try:
        email = normalize_email(args.email)
    except ValueError:
        print("bootstrap failed: invalid email", file=sys.stderr)
        return 2
    if not _valid_email(email):
        print("bootstrap failed: invalid email", file=sys.stderr)
        return 2
    print(f"Bootstrap owner: {email}")
    if not sys.stdin.isatty():
        print("bootstrap failed: password prompt requires an interactive terminal", file=sys.stderr)
        return 2
    try:
        password = getpass.getpass("Password: ")
    except (EOFError, KeyboardInterrupt):
        print("bootstrap failed: password prompt was canceled", file=sys.stderr)
        return 2
    manifest_path = Path(os.environ.get("WORKSPACE_MIGRATION_MANIFEST_PATH", DEFAULT_MANIFEST_PATH))
    try:
        result = bootstrap_owner(email, password, manifest_path=manifest_path)
    except BootstrapError as error:
        print(f"bootstrap failed: {error}", file=sys.stderr)
        return 1
    print(f"user_id={result.user_id}")
    print(f"workspace_id={result.workspace_id}")
    print(f"membership_id={result.membership_id}")
    print(f"release_id={result.release_id}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
