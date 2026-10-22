from concurrent.futures import ThreadPoolExecutor
import threading

import pytest

from capture_node.core.cameras.synchronized_frames import SynchronizedFrames


class Request:
    def __init__(self, timestamp, ready=True):
        self.metadata = {"SensorTimestamp": timestamp, "SyncReady": ready}
        self.references = 0

    def get_metadata(self):
        return self.metadata

    def acquire(self):
        self.references += 1

    def release(self):
        self.references -= 1


def test_selects_ready_frame_after_target_and_releases_ownership():
    frames = SynchronizedFrames()
    early = Request(10)
    unready = Request(30, False)
    selected = Request(30)
    stop = threading.Event()

    def feed():
        while not stop.wait(0.001):
            for request in (early, unready, selected):
                frames.observe(request)

    with ThreadPoolExecutor() as pool:
        producer = pool.submit(feed)
        try:
            request = frames.capture(20, 1)
            assert request is selected
            request.release()
        finally:
            stop.set()
            producer.result(timeout=1)
    frames.close()
    assert [item.references for item in (early, unready, selected)] == [0, 0, 0]


def test_timeout_does_not_retain_late_frames_and_close_unblocks_capture():
    frames = SynchronizedFrames()
    with pytest.raises(TimeoutError):
        frames.capture(10, 0.001)
    late = Request(20)
    frames.observe(late)
    assert late.references == 0
    with ThreadPoolExecutor() as pool:
        capture = pool.submit(frames.capture, 30, 10)
        frames.close()
        with pytest.raises(RuntimeError, match="closed"):
            capture.result(timeout=1)
    frames.observe(late)
    assert late.references == 0
