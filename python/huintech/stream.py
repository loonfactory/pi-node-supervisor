from typing import Protocol


class Stream(Protocol):
    def read(self, size: int = -1) -> bytes:
        ...

    def write(self, data: bytes) -> int:
        ...
