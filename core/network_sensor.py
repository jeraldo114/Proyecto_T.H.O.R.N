"""Contadores e interfaces locales; no captura paquetes ni inspecciona vecinos."""

from __future__ import annotations

import asyncio
import ctypes
import math
import ipaddress
import os
import socket
import struct
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, TypedDict

from .event_bus import EventBus, EventBusClosedError
from .events import EventPriority, ThornEvent


class NetworkInterface(TypedDict):
    name: str
    up: bool
    speed_mbps: int | None
    addresses: list[str]
    bytes_sent: int | None
    bytes_received: int | None
    upload_bps: float | None
    download_bps: float | None


class NetworkSnapshot(TypedDict):
    sampled_at: str
    interfaces: list[NetworkInterface]
    gateway_ipv4: str | None
    upload_bps: float | None
    download_bps: float | None


def default_gateway() -> str | None:
    """Lee la ruta IPv4 por defecto via IP Helper, sin shell ni permisos admin."""
    if os.name == "nt":
        from ctypes import wintypes
        function = ctypes.WinDLL("iphlpapi").GetIpForwardTable
        function.argtypes = [ctypes.c_void_p, ctypes.POINTER(wintypes.ULONG), wintypes.BOOL]
        function.restype = wintypes.ULONG
        size = wintypes.ULONG(0)
        if function(None, ctypes.byref(size), False) != 122 or not 4 <= size.value <= 4 * 1024 * 1024:
            return None
        buffer = ctypes.create_string_buffer(size.value)
        if function(buffer, ctypes.byref(size), False) != 0:
            return None
        raw = buffer.raw
        count = struct.unpack_from("<I", raw)[0]
        candidates = []
        for index in range(min(count, (len(raw) - 4) // 56)):
            row = struct.unpack_from("<14I", raw, 4 + index * 56)
            if row[0] == 0 and row[1] == 0 and row[3] and row[5] == 4:
                candidates.append((row[9], socket.inet_ntoa(struct.pack("<I", row[3]))))
        return min(candidates)[1] if candidates else None
    path = Path("/proc/net/route")
    if path.is_file():
        candidates = []
        for line in path.read_text(encoding="ascii").splitlines()[1:]:
            columns = line.split()
            if len(columns) >= 8 and columns[1] == "00000000" and int(columns[3], 16) & 2:
                candidates.append((int(columns[6]), socket.inet_ntoa(struct.pack("<I", int(columns[2], 16)))))
        return min(candidates)[1] if candidates else None
    return None


def read_network_snapshot() -> NetworkSnapshot:
    """psutil y rutas OS se llaman exclusivamente desde un worker."""
    import psutil
    counters = psutil.net_io_counters(pernic=True)
    stats = psutil.net_if_stats()
    interfaces: list[NetworkInterface] = []
    for name, addresses in psutil.net_if_addrs().items():
        details = stats.get(name)
        count = counters.get(name)
        interfaces.append({"name": name, "up": bool(details and details.isup),
            "speed_mbps": details.speed if details and details.speed > 0 else None,
            "addresses": [item.address for item in addresses if item.family in {socket.AF_INET, socket.AF_INET6}],
            "bytes_sent": count.bytes_sent if count else None,
            "bytes_received": count.bytes_recv if count else None, "upload_bps": None, "download_bps": None})
    return {"sampled_at": datetime.now(timezone.utc).isoformat(), "interfaces": interfaces,
            "gateway_ipv4": default_gateway(), "upload_bps": None, "download_bps": None}


class NetworkSensor:
    """Una lectura cada 2 s. Fallos y primera muestra no fabrican velocidades."""

    def __init__(self, bus: EventBus, *, reader: Callable[[], NetworkSnapshot] = read_network_snapshot,
                 interval: float = 2, clock: Callable[[], float] = time.monotonic) -> None:
        if not math.isfinite(interval) or interval < 0.01:
            raise ValueError("invalid_network_interval")
        self.bus, self.reader = bus, reader
        self.interval, self.clock = interval, clock
        self.task: asyncio.Task[None] | None = None
        self.snapshot: NetworkSnapshot | None = None
        self.sample_time: float | None = None
        self.failed = False
        self.samples = self.errors = 0
        self.closed = False

    @property
    def status(self) -> dict[str, object]:
        age = None if self.sample_time is None else self.clock() - self.sample_time
        return {"running": bool(self.task and not self.task.done() and not self.closed),
                "fresh": bool(not self.closed and not self.failed and age is not None and 0 <= age <= max(6, self.interval * 3)),
                "age_seconds": age, "samples": self.samples, "errors": self.errors}

    async def start(self) -> None:
        if not self.task and not self.closed:
            self.task = asyncio.create_task(self._run(), name="thorn.sensor.network")

    async def _collect(self) -> NetworkSnapshot:
        reading = asyncio.create_task(asyncio.to_thread(self.reader))
        try:
            return await asyncio.shield(reading)
        except asyncio.CancelledError:
            await asyncio.gather(reading, return_exceptions=True)
            raise

    async def _run(self) -> None:
        previous: NetworkSnapshot | None = None
        previous_time: float | None = None
        while True:
            try:
                sample = await self._collect()
                now = self.clock()
                elapsed = now - previous_time if previous_time is not None else 0
                older = {item["name"]: item for item in previous["interfaces"]} if previous else {}
                sent, received, usable, complete = 0.0, 0.0, False, True
                for item in sample["interfaces"]:
                    old = older.get(item["name"])
                    item["upload_bps"] = item["download_bps"] = None
                    if (old and old["up"] and item["up"] and 0.1 <= elapsed <= max(6, self.interval * 3)
                            and all(isinstance(record[key], int) for record in (old, item)
                                    for key in ("bytes_sent", "bytes_received"))
                            and item["bytes_sent"] >= old["bytes_sent"]
                            and item["bytes_received"] >= old["bytes_received"]):
                        item["upload_bps"] = (item["bytes_sent"] - old["bytes_sent"]) / elapsed
                        item["download_bps"] = (item["bytes_received"] - old["bytes_received"]) / elapsed
                    if item["up"] and any(not ipaddress.ip_address(address.split("%")[0]).is_loopback for address in item["addresses"]):
                        if item["upload_bps"] is None:
                            complete = False
                        else:
                            sent += item["upload_bps"]; received += item["download_bps"]; usable = True
                sample["upload_bps"] = sent if usable and complete else None
                sample["download_bps"] = received if usable and complete else None
                self.snapshot, self.sample_time, self.failed = sample, now, False
                self.samples += 1
                previous, previous_time = sample, now
                try:
                    async with asyncio.timeout(0.1):
                        await self.bus.publish(ThornEvent("network.snapshot", "sensor.network",
                            {"samples": self.samples}, priority=EventPriority.LOW))
                except (EventBusClosedError, TimeoutError):
                    pass
            except Exception:
                self.errors += 1; self.failed = True
                previous, previous_time = None, None
            await asyncio.sleep(self.interval)

    async def stop(self) -> None:
        self.closed = True
        if self.task:
            self.task.cancel()
            await asyncio.gather(self.task, return_exceptions=True)
