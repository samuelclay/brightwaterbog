#!/usr/bin/env python3
"""Sync Brightwater Bog full-resolution photo originals with Cloudflare R2.

The website photo originals live under photos/apple-photos-stained-glass/,
which is intentionally ignored by git. This script mirrors that tree to an R2
bucket using R2's S3-compatible API through the AWS CLI.
"""

from __future__ import annotations

import argparse
import os
import shlex
import shutil
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
DEFAULT_ENV = REPO / "tools" / "r2-originals.local.env"
DEFAULT_LOCAL_DIR = REPO / "photos" / "apple-photos-stained-glass"
DEFAULT_PREFIX = "apple-photos-stained-glass"


def parse_env_file(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    if not path.exists():
        return values
    for raw in path.read_text().splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip()
        if not key:
            continue
        if (
            len(value) >= 2
            and value[0] == value[-1]
            and value[0] in {"'", '"'}
        ):
            value = value[1:-1]
        values[key] = value
    return values


def env_value(file_env: dict[str, str], *names: str, default: str | None = None) -> str | None:
    for name in names:
        if file_env.get(name):
            return file_env[name]
        if os.environ.get(name):
            return os.environ[name]
    return default


def require(value: str | None, message: str) -> str:
    if value:
        return value
    raise SystemExit(message)


def s3_uri(bucket: str, prefix: str) -> str:
    clean = prefix.strip("/")
    if clean:
        return f"s3://{bucket}/{clean}/"
    return f"s3://{bucket}/"


def build_config(args: argparse.Namespace) -> dict[str, str]:
    file_env = parse_env_file(args.env)
    account_id = require(
        env_value(file_env, "BWB_R2_ACCOUNT_ID", "R2_ACCOUNT_ID", "CLOUDFLARE_ACCOUNT_ID"),
        f"Missing BWB_R2_ACCOUNT_ID in {args.env}",
    )
    bucket = require(
        env_value(file_env, "BWB_R2_BUCKET", "R2_BUCKET"),
        f"Missing BWB_R2_BUCKET in {args.env}",
    )
    access_key = require(
        env_value(file_env, "BWB_R2_ACCESS_KEY_ID", "AWS_ACCESS_KEY_ID"),
        f"Missing BWB_R2_ACCESS_KEY_ID in {args.env}",
    )
    secret_key = require(
        env_value(file_env, "BWB_R2_SECRET_ACCESS_KEY", "AWS_SECRET_ACCESS_KEY"),
        f"Missing BWB_R2_SECRET_ACCESS_KEY in {args.env}",
    )
    endpoint = env_value(
        file_env,
        "BWB_R2_ENDPOINT_URL",
        "R2_ENDPOINT_URL",
        default=f"https://{account_id}.r2.cloudflarestorage.com",
    )
    prefix = env_value(file_env, "BWB_R2_PREFIX", "R2_PREFIX", default=DEFAULT_PREFIX)
    local_dir = Path(
        env_value(
            file_env,
            "BWB_ORIGINALS_LOCAL_DIR",
            "ORIGINALS_LOCAL_DIR",
            default=str(DEFAULT_LOCAL_DIR),
        )
        or str(DEFAULT_LOCAL_DIR)
    )
    if not local_dir.is_absolute():
        local_dir = REPO / local_dir
    return {
        "account_id": account_id,
        "bucket": bucket,
        "access_key": access_key,
        "secret_key": secret_key,
        "endpoint": endpoint or "",
        "prefix": prefix or "",
        "local_dir": str(local_dir),
    }


def aws_env(config: dict[str, str]) -> dict[str, str]:
    env = os.environ.copy()
    env["AWS_ACCESS_KEY_ID"] = config["access_key"]
    env["AWS_SECRET_ACCESS_KEY"] = config["secret_key"]
    env.setdefault("AWS_DEFAULT_REGION", "auto")
    env.setdefault("AWS_EC2_METADATA_DISABLED", "true")
    env.setdefault("AWS_REQUEST_CHECKSUM_CALCULATION", "when_required")
    env.setdefault("AWS_RESPONSE_CHECKSUM_VALIDATION", "when_required")
    return env


def sync_command(config: dict[str, str], direction: str, *, dry_run: bool, delete: bool) -> list[str]:
    local = Path(config["local_dir"])
    remote = s3_uri(config["bucket"], config["prefix"])
    if direction == "up":
        src = str(local) + "/"
        dst = remote
    elif direction == "down":
        src = remote
        dst = str(local) + "/"
    else:
        raise AssertionError(direction)

    cmd = [
        "aws",
        "s3",
        "sync",
        src,
        dst,
        "--endpoint-url",
        config["endpoint"],
        "--no-progress",
        "--exclude",
        ".DS_Store",
        "--exclude",
        "*/.DS_Store",
    ]
    if dry_run:
        cmd.append("--dryrun")
    if delete:
        cmd.append("--delete")
    return cmd


def print_command(cmd: list[str], config: dict[str, str]) -> None:
    display = ["<r2-endpoint>" if part == config["endpoint"] else part for part in cmd]
    print("+ " + " ".join(shlex.quote(part) for part in display))


def run_sync(config: dict[str, str], args: argparse.Namespace) -> int:
    local = Path(config["local_dir"])
    if args.direction == "up" and not local.is_dir():
        raise SystemExit(f"Local originals directory not found: {local}")
    if args.direction == "down":
        local.mkdir(parents=True, exist_ok=True)
    cmd = sync_command(config, args.direction, dry_run=args.dry_run, delete=args.delete)
    print_command(cmd, config)
    return subprocess.run(cmd, env=aws_env(config), check=False).returncode


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("direction", choices=("up", "down"))
    parser.add_argument(
        "--env",
        type=Path,
        default=DEFAULT_ENV,
        help=f"local env file with R2 settings (default: {DEFAULT_ENV})",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="show planned AWS sync operations without copying files",
    )
    parser.add_argument(
        "--delete",
        action="store_true",
        help="delete destination files missing from the source; off by default",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if shutil.which("aws") is None:
        raise SystemExit("aws CLI not found; install AWS CLI v2 before syncing R2 originals")
    config = build_config(args)
    return run_sync(config, args)


if __name__ == "__main__":
    sys.exit(main())
