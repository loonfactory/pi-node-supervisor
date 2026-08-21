"""Continuous MJPEG streaming from FFmpeg's V4L2 input."""

from __future__ import annotations

import subprocess
import threading
import time
from pathlib import Path
from typing import Any, BinaryIO, Protocol

_JPEG_START = b"\xff\xd8"
_JPEG_END = b"\xff\xd9"
_MAX_FRAME_BUFFER_BYTES = 32 * 1024 * 1024

__all__ = ["FFmpegMjpegStream", "extract_jpeg_frames"]


class _Process(Protocol):
    stdout: BinaryIO | None
    stderr: BinaryIO | None

    def poll(self) -> int | None: ...

    def terminate(self) -> None: ...

    def kill(self) -> None: ...

    def wait(self, timeout: float | None = None) -> int: ...


class FFmpegMjpegStream:
    """Own an FFmpeg process and expose its latest complete JPEG frame."""

    def __init__(
        self,
        *,
        device: Path,
        width: int,
        height: int,
        frame_rate: float,
        startup_timeout_seconds: float,
        process_factory: Any = subprocess.Popen,
    ) -> None:
        self._device = device
        self._startup_timeout = startup_timeout_seconds
        self._condition = threading.Condition()
        self._latest_frame: bytes | None = None
        self._sequence = 0
        self._failure: BaseException | None = None
        self._closed = False
        self._process: _Process = process_factory(
            _ffmpeg_command(device, width, height, frame_rate),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            bufsize=0,
        )
        if self._process.stdout is None:
            self._terminate_process()
            raise RuntimeError("FFmpeg stdout pipe was not created")

        self._reader = threading.Thread(
            target=self._read_frames,
            name="capture-node-v4l2-reader",
            daemon=True,
        )
        self._reader.start()
        try:
            self._wait_until_ready()
        except BaseException:
            self.close()
            raise

    @property
    def closed(self) -> bool:
        with self._condition:
            return self._closed

    @property
    def failure(self) -> BaseException | None:
        with self._condition:
            return self._failure

    @property
    def ready(self) -> bool:
        with self._condition:
            return self._latest_frame is not None

    def current_frame(self) -> tuple[bytes, int]:
        with self._condition:
            if not self._device.exists():
                raise RuntimeError(f"V4L2 device is disconnected: {self._device}")
            if self._latest_frame is not None:
                return self._latest_frame, self._sequence
            if self._failure is not None:
                raise RuntimeError("V4L2 stream is unavailable") from self._failure
            raise RuntimeError("V4L2 stream is not ready")

    def frame_after(self, sequence: int) -> tuple[bytes, int]:
        with self._condition:
            while not self._closed and self._sequence <= sequence:
                if self._failure is not None:
                    raise RuntimeError("V4L2 stream is unavailable") from self._failure
                self._condition.wait(self._startup_timeout)
                if self._sequence <= sequence and self._failure is None:
                    raise TimeoutError("V4L2 stream stopped producing frames")
            if self._closed:
                raise RuntimeError("V4L2 camera is closed")
            assert self._latest_frame is not None
            return self._latest_frame, self._sequence

    def close(self) -> None:
        with self._condition:
            if self._closed:
                return
            self._closed = True
            self._condition.notify_all()
        self._terminate_process()
        self._reader.join(timeout=2)

    def _read_frames(self) -> None:
        assert self._process.stdout is not None
        buffer = b""
        try:
            while True:
                chunk = self._process.stdout.read(64 * 1024)
                if not chunk:
                    break
                buffer += chunk
                if len(buffer) > _MAX_FRAME_BUFFER_BYTES:
                    last_start = buffer.rfind(_JPEG_START)
                    buffer = buffer[last_start:] if last_start >= 0 else b""
                frames, buffer = extract_jpeg_frames(buffer)
                for frame in frames:
                    with self._condition:
                        self._latest_frame = frame
                        self._sequence += 1
                        self._condition.notify_all()
            with self._condition:
                if not self._closed:
                    self._failure = RuntimeError(self._process_failure_detail())
                    self._condition.notify_all()
        except BaseException as exc:
            with self._condition:
                self._failure = exc
                self._condition.notify_all()

    def _wait_until_ready(self) -> None:
        deadline = time.monotonic() + self._startup_timeout
        with self._condition:
            while self._latest_frame is None:
                if self._failure is not None:
                    raise RuntimeError("V4L2 stream failed to start") from self._failure
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise TimeoutError(
                        f"V4L2 stream produced no frame within {self._startup_timeout}s"
                    )
                self._condition.wait(remaining)

    def _terminate_process(self) -> None:
        if self._process.poll() is not None:
            return
        self._process.terminate()
        try:
            self._process.wait(timeout=2)
        except subprocess.TimeoutExpired:
            self._process.kill()
            self._process.wait(timeout=2)

    def _process_failure_detail(self) -> str:
        stderr = self._process.stderr
        detail = stderr.read().decode("utf-8", errors="replace").strip() if stderr else ""
        return detail or f"FFmpeg exited with status {self._process.poll()}"


def extract_jpeg_frames(data: bytes) -> tuple[list[bytes], bytes]:
    """Extract complete JPEG frames and retain a partial trailing frame."""

    frames: list[bytes] = []
    offset = 0
    while True:
        start = data.find(_JPEG_START, offset)
        if start < 0:
            return frames, data[-1:] if data.endswith(b"\xff") else b""
        end = data.find(_JPEG_END, start + len(_JPEG_START))
        if end < 0:
            return frames, data[start:]
        end += len(_JPEG_END)
        frames.append(data[start:end])
        offset = end


def _ffmpeg_command(
    device: Path,
    width: int,
    height: int,
    frame_rate: float,
) -> list[str]:
    return [
        "ffmpeg",
        "-nostdin",
        "-hide_banner",
        "-loglevel",
        "error",
        "-f",
        "v4l2",
        "-input_format",
        "mjpeg",
        "-video_size",
        f"{width}x{height}",
        "-framerate",
        f"{frame_rate:g}",
        "-i",
        str(device),
        "-an",
        "-c:v",
        "copy",
        "-f",
        "mjpeg",
        "pipe:1",
    ]
