"""Binary layout for DaySnapshot files.

Header layout matches the Rust native engine (little-endian, fixed 32 bytes):
   offset 0:  magic            4 bytes  "ITDS"
   offset 4:  version          1 byte   (currently 1)
   offset 5:  symbol           16 bytes (utf-8, null-padded)
   offset 21: trade_date_days  4 bytes  (i32, days since 1970-01-01)
   offset 25: expiry_count     1 byte   (u8)
   offset 26: minute_count     2 bytes  (u16)
   offset 28: reserved         4 bytes  (zeroed)
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

# Layout: magic(4) version(1) symbol(16) days(4) expiry_count(1) minute_count(2) pad(4) = 32
_HEADER_STRUCT = struct.Struct("<4sB16siB H 4x")
assert _HEADER_STRUCT.size == HEADER_BYTES


def pack_header(
    *, symbol: str, trade_date: date, expiry_count: int,
    minute_count: int = MINUTES_PER_DAY,
) -> bytes:
    sym_bytes = symbol.encode("utf-8")
    if len(sym_bytes) > SYMBOL_FIELD_LEN:
        raise ValueError(f"symbol too long: {symbol!r}")
    sym_padded = sym_bytes.ljust(SYMBOL_FIELD_LEN, b"\x00")
    days = (trade_date - date(1970, 1, 1)).days
    return _HEADER_STRUCT.pack(MAGIC, VERSION, sym_padded, days, expiry_count, minute_count)


def unpack_header(buf: bytes) -> dict:
    if len(buf) < HEADER_BYTES:
        raise ValueError(f"buffer too small: {len(buf)} < {HEADER_BYTES}")
    magic, version, sym_padded, days, expiry_count, minute_count = _HEADER_STRUCT.unpack_from(buf, 0)
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
        "minute_count": minute_count,
    }
