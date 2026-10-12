"""Offline config validation and explicitly enabled bounded protocol inspection."""

import argparse
import asyncio
from pathlib import Path
import time
import json
import sys
from collections.abc import Sequence

from pydantic import ValidationError

from .capabilities import pending_capabilities
from .config import ConfigError, config_digest, load_config


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="btc-microstructure")
    commands = parser.add_subparsers(dest="command", required=True)
    validate = commands.add_parser("validate-config", help="validate a local YAML file offline")
    validate.add_argument("path")
    validate_offline = commands.add_parser(
        "validate-offline", help="validate the strict R1-03 synthetic offline config"
    )
    validate_offline.add_argument("path")
    collect = commands.add_parser("collect", help="R1-03 synthetic offline collection only")
    collect.add_argument("path", help="strict offline config")
    collect.add_argument("--mode", choices=["synthetic"], required=True)
    collect.add_argument("--output", required=True, type=Path)
    replay = commands.add_parser("replay", help="deterministically replay an R1-03 offline run")
    replay.add_argument("path", help="strict offline config")
    replay.add_argument("--input", required=True, type=Path)
    replay.add_argument("--output", required=True, type=Path)
    build_features_command = commands.add_parser(
        "build-features", help="rebuild R1-03 derived tables from committed raw/control logs"
    )
    build_features_command.add_argument("path", help="strict offline config")
    build_features_command.add_argument("--input", required=True, type=Path)
    build_features_command.add_argument("--output", required=True, type=Path)
    probe = commands.add_parser("probe", help="bounded BTCUSDT perpetual metadata + aggTrade only")
    probe.add_argument("path", help="local research config")
    probe.add_argument("--output", required=True, type=Path, help="new evidence directory")
    probe.add_argument(
        "--allow-network", action="store_true", help="explicit opt-in to real public access"
    )
    probe.add_argument("--seconds", type=float, default=30.0)
    probe.add_argument("--rest-total-seconds", type=float, default=None)
    probe.add_argument("--messages", type=int, default=20)
    probe.add_argument("--max-bytes", type=int, default=2097152)
    args = parser.parse_args(argv)
    try:
        if args.command in {"validate-offline", "collect", "replay", "build-features"}:
            from .offline import OfflineConfig, build_features, replay_run, run_synthetic

            offline_config = OfflineConfig.load(args.path)
            if args.command == "validate-offline":
                print(
                    json.dumps(
                        {
                            "status": "OFFLINE_CONFIG_VALID",
                            "sha256": offline_config.digest(),
                            "input_mode": "synthetic",
                            "network_access": "DISABLED",
                        },
                        sort_keys=True,
                    )
                )
                return 0
            if args.command == "collect":
                result = run_synthetic(offline_config, args.output)
            elif args.command == "replay":
                result = replay_run(offline_config, args.input, args.output)
            else:
                result = build_features(offline_config, args.input, args.output)
            print(json.dumps(result, sort_keys=True))
            return 0 if result["status"] == "PASS" else 1
        config = load_config(args.path)
        if args.command == "probe":
            from .probe import ProbeLimits, run_probe, save_probe

            limits = ProbeLimits(
                total_seconds=args.seconds,
                rest_total_seconds=args.rest_total_seconds,
                max_messages=args.messages,
                max_bytes=args.max_bytes,
            )
            if (
                config.endpoints.um_rest != "https://fapi.binance.com"
                or config.endpoints.um_market_ws != "wss://fstream.binance.com/market/stream"
            ):
                raise ConfigError(
                    "endpoints.um_rest/um_market_ws: probe only allows documented public Binance routes"
                )
            if args.output.exists():
                raise ConfigError("output: refusing to overwrite previous evidence")
            if not args.allow_network:
                args.output.mkdir(parents=True)
                (args.output / "protocol_probe_report.json").write_text(
                    json.dumps(
                        {
                            "status": "NOT_RUN",
                            "evidence_kind": "LIVE",
                            "recorded_ns": time.time_ns(),
                            "reason": "explicit --allow-network required",
                            "limits": limits.model_dump(),
                            "continuous_collection_enabled": False,
                        },
                        indent=2,
                    )
                    + "\n"
                )
                (args.output / "endpoint_budget.json").write_text(
                    json.dumps(
                        {
                            "status": "NOT_RUN",
                            "evidence_kind": "LIVE",
                            "scope": "BTCUSDT um_perpetual metadata + aggTrade",
                            "observed_rate_limits": None,
                            "probe_limits": limits.model_dump(),
                            "continuous_collection_enabled": False,
                        },
                        indent=2,
                    )
                    + "\n"
                )
                print("NOT_RUN: explicit --allow-network required")
                return 2
            from .probe_transport import AioTransport

            result = asyncio.run(run_probe(AioTransport(), limits, evidence_kind="LIVE"))
            result["config_sha256"] = config_digest(config)
            save_probe(result, args.output)
            print(
                json.dumps(
                    {
                        "status": result["status"],
                        "reason": result["reason"],
                        "output": str(args.output),
                    }
                )
            )
            return 0 if result["status"] == "PASS" else 1
    except ValidationError as exc:
        for error in exc.errors(include_input=False, include_url=False, include_context=False):
            field = ".".join(str(part) for part in error["loc"]) or "config"
            print(f"{field}: {error['msg']}", file=sys.stderr)
        return 1
    except (ConfigError, OSError) as exc:
        print(str(exc), file=sys.stderr)
        return 1
    print(
        json.dumps(
            {
                "status": "CONFIG_VALID",
                "sha256": config_digest(config),
                "runtime_capabilities": {
                    item.name: item.status for item in pending_capabilities(config)
                },
            },
            sort_keys=True,
        )
    )
    return 0
