#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from dataclasses import asdict

from hermes_control.config import load_config
from hermes_control.monitor import StatusProbe
from hermes_control.monitor import stack_readiness


def main() -> int:
    parser = argparse.ArgumentParser(description="Hermes-Helper local AI control center")
    parser.add_argument("--startup", action="store_true", help="launch minimized after graphical login")
    parser.add_argument("--status-json", action="store_true", help="print a non-secret status snapshot and exit")
    args = parser.parse_args()
    if args.status_json:
        config = load_config()
        snap = StatusProbe(config).snapshot()
        readiness = stack_readiness(snap, config)
        payload = {
            "ollama_reachable": snap.ollama_reachable,
            "gateway_pids": [item.pid for item in snap.gateway],
            "gateways": [
                {
                    "pid": item.pid,
                    "owner_uid": item.owner_uid,
                    "controllable": item.controllable,
                }
                for item in snap.gateway
            ],
            "running_models": [item.get("name", "") for item in snap.running_models],
            "inference_mode": "cloud" if readiness.cloud_mode else "local",
            "active_model": readiness.active_model,
            "active_provider": readiness.provider,
            "local_chat_ready": readiness.local_chat_ready,
            "full_stack_ready": readiness.full_stack_ready,
            "readiness_blockers": list(readiness.blockers),
            "telegram_configured": snap.telegram.configured,
            "telegram_reachable": snap.telegram.reachable,
            "cpu_temperature": asdict(snap.cpu_temp) if snap.cpu_temp else None,
            "diagnostics": snap.diagnostics,
        }
        print(json.dumps(payload, indent=2))
        return 0
    from hermes_control.gui import HermesHelperApp
    HermesHelperApp(startup=args.startup).run()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
