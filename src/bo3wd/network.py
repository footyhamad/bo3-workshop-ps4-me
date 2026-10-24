"""Lightweight system-wide network throughput sampler."""

from __future__ import annotations

import ctypes
import os
import time
from pathlib import Path
from typing import Callable

CounterMap = dict[str, tuple[int, int]]
_LOOPBACK_IF_TYPE = 24
_IF_OPER_STATUS_UP = 1
_COUNTER_MASK_32 = (1 << 32) - 1


def _read_proc_net_dev(path: Path = Path("/proc/net/dev")) -> CounterMap:
    counters: CounterMap = {}
    for line in path.read_text(encoding="ascii", errors="replace").splitlines()[2:]:
        if ":" not in line:
            continue
        name, values = line.split(":", 1)
        fields = values.split()
        if len(fields) < 16:
            continue
        try:
            received = int(fields[0])
            sent = int(fields[8])
        except ValueError:
            continue
        counters[name.strip()] = (received, sent)
    return counters


def _read_windows_if_table() -> CounterMap:
    """Read operational non-loopback adapter counters through IP Helper (no extra dependency)."""
    if os.name != "nt":
        raise OSError("Windows network counters are only available on Windows.")

    class MIB_IFROW(ctypes.Structure):
        _fields_ = [
            ("wszName", ctypes.c_wchar * 256),
            ("dwIndex", ctypes.c_ulong),
            ("dwType", ctypes.c_ulong),
            ("dwMtu", ctypes.c_ulong),
            ("dwSpeed", ctypes.c_ulong),
            ("dwPhysAddrLen", ctypes.c_ulong),
            ("bPhysAddr", ctypes.c_ubyte * 8),
            ("dwAdminStatus", ctypes.c_ulong),
            ("dwOperStatus", ctypes.c_ulong),
            ("dwLastChange", ctypes.c_ulong),
            ("dwInOctets", ctypes.c_ulong),
            ("dwInUcastPkts", ctypes.c_ulong),
            ("dwInNUcastPkts", ctypes.c_ulong),
            ("dwInDiscards", ctypes.c_ulong),
            ("dwInErrors", ctypes.c_ulong),
            ("dwInUnknownProtos", ctypes.c_ulong),
            ("dwOutOctets", ctypes.c_ulong),
            ("dwOutUcastPkts", ctypes.c_ulong),
            ("dwOutNUcastPkts", ctypes.c_ulong),
            ("dwOutDiscards", ctypes.c_ulong),
            ("dwOutErrors", ctypes.c_ulong),
            ("dwOutQLen", ctypes.c_ulong),
            ("dwDescrLen", ctypes.c_ulong),
            ("bDescr", ctypes.c_ubyte * 256),
        ]

    iphlpapi = ctypes.WinDLL("iphlpapi.dll")
    get_if_table = iphlpapi.GetIfTable
    get_if_table.argtypes = [
        ctypes.c_void_p,
        ctypes.POINTER(ctypes.c_ulong),
        ctypes.c_int,
    ]
    get_if_table.restype = ctypes.c_ulong

    size = ctypes.c_ulong(0)
    result = get_if_table(None, ctypes.byref(size), 1)
    if result not in (0, 122):  # NO_ERROR / ERROR_INSUFFICIENT_BUFFER
        raise OSError(int(result), "GetIfTable could not size the adapter table")
    if size.value <= ctypes.sizeof(ctypes.c_ulong):
        return {}

    buffer = ctypes.create_string_buffer(size.value)
    result = get_if_table(buffer, ctypes.byref(size), 1)
    if result:
        raise OSError(int(result), "GetIfTable could not read the adapter table")

    count = ctypes.cast(buffer, ctypes.POINTER(ctypes.c_ulong)).contents.value
    row_size = ctypes.sizeof(MIB_IFROW)
    base = ctypes.addressof(buffer) + ctypes.sizeof(ctypes.c_ulong)
    counters: CounterMap = {}
    for index in range(min(int(count), 4096)):
        row = MIB_IFROW.from_address(base + index * row_size)
        if row.dwOperStatus != _IF_OPER_STATUS_UP or row.dwType == _LOOPBACK_IF_TYPE:
            continue
        counters[str(int(row.dwIndex))] = (int(row.dwInOctets), int(row.dwOutOctets))
    return counters


def read_network_counters() -> CounterMap:
    """Return receive/transmit byte totals per interface for the current OS."""
    if os.name == "nt":
        return _read_windows_if_table()
    proc_net_dev = Path("/proc/net/dev")
    if proc_net_dev.is_file():
        return _read_proc_net_dev(proc_net_dev)
    raise OSError("Network-interface byte counters are unavailable on this system.")


def _counter_delta(before: int, after: int) -> int:
    if after >= before:
        return after - before
    # Windows GetIfTable exposes 32-bit octet counters; account for normal wraparound.
    if before > 0xF0000000 and after < 0x10000000:
        return (after - before) & _COUNTER_MASK_32
    # An adapter reset/reconnect should not show as a huge throughput spike.
    return 0


class NetworkSpeedMeter:
    """Sample total system RX/TX throughput; it is not scoped to SteamCMD alone."""

    def __init__(
        self,
        reader: Callable[[], CounterMap] | None = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._reader = reader or read_network_counters
        self._clock = clock
        self._previous: CounterMap | None = None
        self._sampled_at: float | None = None

    def sample(self) -> tuple[float, float]:
        counters = self._reader()
        now = self._clock()
        previous = self._previous
        previous_at = self._sampled_at
        self._previous = counters
        self._sampled_at = now
        if previous is None or previous_at is None:
            return 0.0, 0.0

        elapsed = now - previous_at
        if elapsed <= 0:
            return 0.0, 0.0
        received = 0
        sent = 0
        for name, (rx_after, tx_after) in counters.items():
            old = previous.get(name)
            if old is None:
                continue
            received += _counter_delta(old[0], rx_after)
            sent += _counter_delta(old[1], tx_after)
        return received / elapsed, sent / elapsed
