"""Headless process entry point."""

from __future__ import annotations

import argparse
import logging
import signal
import threading
from pathlib import Path
from types import FrameType

from capture_node.core import CaptureNode
from capture_node.core.cameras import ConfiguredCameraProvider
from capture_node.core.config import Configuration
from capture_node.core.storages import ConfiguredStorageProvider
from capture_node.headless.logging_config import configure_logging


def create_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run CaptureNode headlessly")
    parser.add_argument(
        "--config",
        type=Path,
        required=True,
        help="path to the JSON options document",
    )
    parser.add_argument(
        "--log-level",
        default="INFO",
        choices=("DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"),
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    arguments = create_parser().parse_args(argv)
    configure_logging(arguments.log_level)
    logger = logging.getLogger("capture_node.headless")
    camera_provider: ConfiguredCameraProvider | None = None
    node = None
    stopping = threading.Event()

    def request_stop(_signum: int, _frame: FrameType | None) -> None:
        stopping.set()

    try:
        configuration = Configuration.load(arguments.config)
        camera_provider = ConfiguredCameraProvider(configuration.current.camera)
        storage_provider = ConfiguredStorageProvider(configuration.current.storages)
        node = CaptureNode(
            camera_provider,
            configuration,
            storage_provider,
        )
        signal.signal(signal.SIGINT, request_stop)
        signal.signal(signal.SIGTERM, request_stop)
        node.start()
        stopping.wait()
        return 0
    except BaseException:
        logger.exception("CaptureNode headless startup or runtime failure")
        return 2
    finally:
        if node is not None:
            node.stop()
        if camera_provider is not None:
            camera_provider.close()


if __name__ == "__main__":
    raise SystemExit(main())
