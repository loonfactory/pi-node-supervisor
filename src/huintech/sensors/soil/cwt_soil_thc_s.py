"""Driver for the CWT-Soil-THC-S soil sensor."""

from huintech.stream import Stream


class CWTSoilTHCS:
    def __init__(self, stream: Stream, slave_id: int = 1):
        self._stream = stream
        self._slave_id = slave_id
