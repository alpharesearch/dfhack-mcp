"""
Low-level DFHack RPC transport, with lazy connect and automatic reconnect.

Design notes
------------
DFHack's remote server allocates a method id *per TCP connection* (see BindMethod,
id 0). A connection that survives a game restart would keep calling ids bound to the
previous game process, so every new connection generation clears the BindMethod cache.

The socket is also shared by every concurrent tool call in the MCP server, so a
request/response exchange is serialized under ``_io_lock``: without it, two parallel
calls interleave their frames on one socket and responses get matched to the wrong
request.

Endpoint resolution honours DFHack's own precedence: ``DFHACK_PORT`` overrides
``dfhack-config/remote-server.json``, which overrides the default 5000.
"""

import asyncio
import os
import sys
from enum import IntEnum
from functools import update_wrapper
from inspect import signature, Parameter

from async_lru import alru_cache

from .py_export.CoreProtocol_pb2 import (
    EmptyMessage, CoreBindReply, CoreBindRequest, CoreTextNotification,
    CoreErrorNotification,
)

# DFHack: "the DFHACK_PORT environment variable takes precedence" over remote-server.json
DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 5000

HANDSHAKE_REQUEST_MAGIC = b"DFHack?\n"
HANDSHAKE_REPLY_MAGIC = b"DFHack!\n"
HANDSHAKE_VERSION = 1
HANDSHAKE_REPLY_LEN = 12


_reply_log: list[str] = []                     # DFHack console text, per call (see drain_replies)


def drain_replies() -> list[str]:
    """Return and clear the DFHack console text captured since the last drain.

    CoreProtocol.RunCommand answers EmptyMessage; everything the command printed
    arrives as RPC_REPLY_TEXT frames. Those frames are consumed inside _invoke, so
    without this hook a tool can only say "sent" - the caller never sees `autochop
    status`, `buildingplan status`, `quickfort orders --dry-run`, `workorder -l`,
    or `help`, which is exactly the feedback a player acts on.
    Calls are serialized under _io_lock, so draining right after a call attributes
    the text to that call.
    """
    out = list(_reply_log)
    _reply_log.clear()
    return out


_reader, _writer = None, None
_generation = 0                                   # bumped on every successful connect
_io_lock: asyncio.Lock | None = None              # one request/response at a time
_connect_lock: asyncio.Lock | None = None


def _locks():
    """Create the asyncio locks lazily, bound to the running event loop."""
    global _io_lock, _connect_lock
    if _io_lock is None:
        _io_lock = asyncio.Lock()
    if _connect_lock is None:
        _connect_lock = asyncio.Lock()


class DFHackError(Exception):
    """Exception raised when a DFHack command fails"""

    def __init__(self, code, message=None):
        self.code = code
        try:
            self.code_name = CoreErrorNotification.ErrorCode.Name(code)
        except ValueError:
            self.code_name = f"Unknown({code})"
        self.message = message or f"DFHack command failed with error code: {self.code} ({self.code_name})"
        super().__init__(self.message)


class DFHackTransportError(ConnectionError):
    """Raised when the RPC socket itself is unusable: no game, dropped game,
    short read, or a rejected handshake. The transport reconnects once on this."""


class DFHackReplyCode(IntEnum):
    RPC_REPLY_RESULT = -1
    RPC_REPLY_FAIL = -2
    RPC_REPLY_TEXT = -3
    RPC_REQUEST_QUIT = -4


def header(id, size):
    return id.to_bytes(2, sys.byteorder, signed=True) + \
        b'\x00\x00' + \
        size.to_bytes(4, sys.byteorder)


def request(id, msg):
    s = msg.SerializeToString()
    h = header(id, len(s))
    return h + s


def handshake_request():
    return HANDSHAKE_REQUEST_MAGIC + HANDSHAKE_VERSION.to_bytes(4, sys.byteorder, signed=False)


async def _read_exact(reader, n, why, on_short=None):
    """Read exactly n bytes. b'' from read() means the peer closed: that must raise,
    never loop. (The original `while size: read(size)` spun forever when the game
    exited mid-response.)"""
    buf = bytearray()
    while len(buf) < n:
        chunk = await reader.read(n - len(buf))
        if not chunk:
            if on_short:
                on_short()
            raise DFHackTransportError(
                f"DFHack closed the connection while reading {why} "
                f"({len(buf)}/{n} bytes) - the game probably exited")
        buf.extend(chunk)
    return bytes(buf)


def is_connected():
    """True when a live RPC socket is held."""
    return _writer is not None and not _writer.is_closing()


def connection_generation():
    """Connection counter; changes on every (re)connect. 0 means never connected."""
    return _generation


def connection_state():
    """Serializable snapshot for status/tooling."""
    host, port = _endpoint()
    info = BindMethod.cache_info() if hasattr(BindMethod, "cache_info") else None
    return {
        "connected": is_connected(),
        "generation": _generation,
        "host": host,
        "port": port,
        "boundMethods": 0 if info is None else int(getattr(info, "currsize", 0)),
    }


def _endpoint():
    """Resolve the RPC endpoint at connect time. DFHack's own precedence:
    DFHACK_PORT > remote-server.json port > 5000. DFHACK_RPC_HOST/DFHACK_RPC_PORT
    are this client's overrides for multi-instance setups."""
    host = os.getenv("DFHACK_RPC_HOST") or DEFAULT_HOST
    port = os.getenv("DFHACK_PORT") or os.getenv("DFHACK_RPC_PORT")
    return host, int(port) if port else DEFAULT_PORT


def _drop():
    """Close the local socket and mark the transport disconnected, without sending
    quit. Next ensure_connection() opens a fresh generation."""
    global _reader, _writer
    writer, _reader, _writer = _writer, None, None
    if writer is not None:
        try:
            writer.close()
        except Exception:
            pass
    BindMethod.cache_clear()


def drop_connection():
    """Force the next call to reconnect (test hook / recovery escape hatch)."""
    _drop()
    return _generation


async def _read_reply_code(reader):
    """Handshake reply, exactly 12 bytes."""
    return await _read_exact(reader, HANDSHAKE_REPLY_LEN, "the handshake reply")


async def connect(host=None, port=None):
    """Open a fresh RPC connection and complete the handshake.

    Returns the new generation number. Raises DFHackTransportError when the game
    is unreachable, so callers get a readable message instead of a raw OSError.
    """
    global _reader, _writer, _generation
    _locks()
    host, port = (host, port) if (host and port) else _endpoint()
    try:
        reader, writer = await asyncio.open_connection(host, port)
    except OSError as e:
        raise DFHackTransportError(
            f"cannot reach DFHack RPC at {host}:{port}: {e}. "
            f"Start Dwarf Fortress with DFHack (remote-server plugin listens on "
            f"{port}; check dfhack-config/remote-server.json)") from e

    try:
        writer.write(handshake_request())
        await writer.drain()
        reply = await _read_reply_code(reader)
    except Exception:
        try:
            writer.close()
        except Exception:
            pass
        raise

    if reply[:8] != HANDSHAKE_REPLY_MAGIC:
        writer.close()
        raise DFHackTransportError(
            f"unexpected handshake reply from {host}:{port}: {reply!r} "
            f"(is a DFHack remote-server really listening there?)")

    _reader, _writer = reader, writer
    _generation += 1
    # Method ids are assigned per connection, so ids bound to the previous game
    # process are invalid now.
    BindMethod.cache_clear()
    return _generation


async def ensure_connection():
    """Return a live generation, connecting if needed. Serialized so parallel calls
    open one socket, not one each."""
    _locks()
    if is_connected():
        return _generation
    async with _connect_lock:
        if is_connected():
            return _generation
        return await connect()


async def get_header():
    if _reader is None:
        raise DFHackTransportError("not connected to DFHack")
    h = await _read_exact(_reader, 8, "a response header")
    id = int.from_bytes(h[0:2], sys.byteorder, signed=True)
    size = int.from_bytes(h[4:8], sys.byteorder)

    # For RPC_REPLY_FAIL, the size field is reused for command_result
    if id == DFHackReplyCode.RPC_REPLY_FAIL:
        raise DFHackError(size)

    return id, size


def unmarshal(id, msg):
    if id == DFHackReplyCode.RPC_REPLY_RESULT:
        obj = CoreBindReply()
        obj.ParseFromString(msg)
        return obj.assigned_id
    elif id == DFHackReplyCode.RPC_REPLY_TEXT:
        obj = CoreTextNotification()
        obj.ParseFromString(msg)
        raise Exception(obj)
    elif id == DFHackReplyCode.RPC_REPLY_FAIL:
        error_code = int.from_bytes(msg, sys.byteorder, signed=True)
        raise DFHackError(error_code)
    else:
        raise DFHackError(f"Unknown reply code: {id}")


@alru_cache(maxsize=65534)
async def BindMethod(method, input_msg, output_msg, plugin=''):
    """Issue a CoreBindRequest to DFHack and caches the returned identifier number.
    Cleared on every new connection - ids are per-connection."""
    br = CoreBindRequest()
    br.method, br.input_msg, br.output_msg, br.plugin = \
        method, input_msg.DESCRIPTOR.full_name, output_msg.DESCRIPTOR.full_name, plugin
    _writer.write(request(0, br))
    await _writer.drain()
    id, size = await get_header()
    return unmarshal(id, await _read_exact(_reader, size, "the bind reply"))


async def close():
    """Send the quit frame and close. Safe to call when already disconnected."""
    if not is_connected():
        _drop()
        return
    buf = header(DFHackReplyCode.RPC_REQUEST_QUIT, 0) + b'\x00\x00\x00\x00'
    try:
        _writer.write(buf)
        await _writer.drain()
        _writer.close()
        await _writer.wait_closed()
    finally:
        _drop()


def get_param_type(param):
    if param and param.annotation != Parameter.empty:
        return param.annotation
    return EmptyMessage


# How many connect attempts one tool call may make: the first use, plus one
# reconnect after a dropped socket.
MAX_CALL_ATTEMPTS = 2


def remote(func=None, *, plugin=''):
    """Decorator for DFHack remote functions.

    Each call runs on a guaranteed-live connection: a dropped socket (game exited,
    world reloaded, port closed) is detected, the transport reconnects, method ids
    are re-bound, and the call is retried once.

    Usage:
        @remote
        async def GetVersion(output: StringMessage = None):
            pass

        @remote(plugin='RemoteFortressReader')
        async def GetVersionInfo(input: EmptyMessage = None, output: VersionInfo = None):
            pass
    """

    def decorator(f):
        params = signature(f).parameters
        input_type = get_param_type(params.get('input'))
        output_type = get_param_type(params.get('output'))

        async def _invoke(**kwargs):
            _id = await BindMethod(f.__name__, input_type, output_type, plugin=plugin)
            input_value = kwargs.get('input') if kwargs.get('input') is not None else input_type()
            _writer.write(request(_id, input_value))
            await _writer.drain()

            # The server may send zero or more RPC_REPLY_TEXT frames, then
            # RPC_REPLY_RESULT or RPC_REPLY_FAIL.
            while True:
                id, size = await get_header()

                if id == DFHackReplyCode.RPC_REPLY_TEXT:
                    buffer = await _read_exact(_reader, size, "a text notification")
                    text_obj = CoreTextNotification()
                    text_obj.ParseFromString(buffer)
                    text = "".join(f.text for f in text_obj.fragments)
                    print("[INFO] DFHack: " + text)
                    _reply_log.append(text)          # so a caller can return it to the model
                    continue
                elif id == DFHackReplyCode.RPC_REPLY_RESULT:
                    buffer = await _read_exact(_reader, size, "a result payload")
                    obj = output_type()
                    obj.ParseFromString(buffer)
                    return obj
                elif id == DFHackReplyCode.RPC_REPLY_FAIL:
                    raise DFHackError(size)
                else:
                    raise DFHackError(f"Unexpected reply code: {id}")

        async def wrapper(*args, **kwargs):
            last = None
            for _ in range(MAX_CALL_ATTEMPTS):
                gen = await ensure_connection()
                try:
                    async with _io_lock:
                        return await _invoke(**kwargs)
                except DFHackTransportError as e:
                    # The socket we just used is gone. Drop it only if nobody else
                    # already replaced this generation.
                    last = e
                    if _generation == gen:
                        _drop()
                    continue
            raise last

        return update_wrapper(wrapper, f)

    if func is None:
        return decorator
    return decorator(func)
