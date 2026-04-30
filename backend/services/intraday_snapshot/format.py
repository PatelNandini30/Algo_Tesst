"""Binary layout for DaySnapshot files.

Header layout (little-endian, fixed 32 bytes):
   offset 0:  magic            4 bytes  "ITDS"
   offset 4:  version          1 byte   (currently 1)
   offset 5:  reserved         3 bytes  (zeroed)
   offset 8:  symbol           16 bytes (utf-8, null-padded)
   offset 24: trade_date_days  4 bytes  (i32, days since 1970-01-01)
   offset 28: expiry_count     1 byte   (u8)
   offset 29: reserved         3 bytes  (zeroed)
"""
import struct
from datetime import date, timedelta

MAGIC = b"ITDS"
VERSION = 1
MINUTES_PER_DAY = 375  # 09:15..15:30 IST inclusive
STRIKE_RADIUS = 5
STRIKES_IN_CHAIN = STRIKE_RADIUS * 2 + 1  # 11
OPT_TYPES = 2  # CE, PE
HEADER_BYTES = 32
SYMBOL_FIELD_LEN = 16

_HEADER_STRUCT = struct.Struct("<4sB3x16si B 3x")  # 4+1+3+16+4+1+3 = 32 bytes
assert _HEADER_STRUCT.size == HEADER_BYTES


def pack_header(*, symbol: str, trade_date: date, expiry_count: int) -> bytes:
    sym_bytes = symbol.encode("utf-8")
    if len(sym_bytes) > SYMBOL_FIELD_LEN:
        raise ValueError(f"symbol too long: {symbol!r}")
    sym_padded = sym_bytes.ljust(SYMBOL_FIELD_LEN, b"\x00")
    days = (trade_date - date(1970, 1, 1)).days
    return _HEADER_STRUCT.pack(MAGIC, VERSION, sym_padded, days, expiry_count)


def unpack_header(buf: bytes) -> dict:
    if len(buf) < HEADER_BYTES:
        raise ValueError(f"buffer too small: {len(buf)} < {HEADER_BYTES}")
    magic, version, sym_padded, days, expiry_count = _HEADER_STRUCT.unpack_from(buf, 0)
    if magic != MAGIC:
        raise ValueError(f"bad magic: {magic!r}")
    if version != VERSION:
        raise ValueError(f"unsupported version: {version}")
    return {
        "magic": magic,
        "version": version,
        "symbol": sym_padded.rstrip(b"\x00").decode("utf-8"),
        "trade_date": date(1970, 1, 1) + timedelta(days=days),
        "expiry_count": expiry_count,
    }
