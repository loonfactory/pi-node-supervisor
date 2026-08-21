#!/usr/bin/env python3
"""Read temperature and humidity once without exposing any sensor writes."""

from __future__ import annotations

import argparse
import json

from pi_node_supervisor.models import NodeDefinition
from pi_node_supervisor.sensor_adapter import HuintechCwtProvider


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", default="/dev/ttyAMA0")
    parser.add_argument("--baudrate", type=int, default=4800)
    parser.add_argument("--timeout", type=float, default=1.0)
    parser.add_argument("--slave-id", type=int, default=1)
    args = parser.parse_args()
    node = NodeDefinition.model_validate(
        {
            "id": "cwt-smoke",
            "kind": "sensor-node",
            "activity_types": ["sensor-collection"],
            "profiles": ["temperature-humidity"],
            "sensor_serial": {
                "driver": "cwt-soil-thc-s",
                "port": args.port,
                "baudrate": args.baudrate,
                "timeout_seconds": args.timeout,
                "slave_id": args.slave_id,
            },
        }
    )
    provider = HuintechCwtProvider(node)
    try:
        reading = provider.read()
    finally:
        provider.close()
    print(json.dumps(reading.model_dump(mode="json"), ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
