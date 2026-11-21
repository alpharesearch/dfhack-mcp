"""Mock DFHack remote-server: handshake + BindMethod + a couple of methods.

Used to prove the transport survives a game restart (new process, new port owner,
DIFFERENT method ids) without restarting the client/server process.

    python mock_dfhack.py <port> <id_offset> <label>
"""
import asyncio
import pathlib
import struct
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2] / "dfhack-client-python" / "src"))
from dfhack_client_python.py_export.CoreProtocol_pb2 import (  # noqa: E402
    CoreBindRequest, CoreBindReply, StringMessage,
)

PORT = int(sys.argv[1])
ID_OFFSET = int(sys.argv[2])
LABEL = sys.argv[3]

# method name -> (assigned id, output message)
METHODS = {
    "GetVersion": (ID_OFFSET + 1, StringMessage(value=f"dfhack {LABEL}")),
    "GetDFVersion": (ID_OFFSET + 2, StringMessage(value=f"df {LABEL} linux64")),
}
BOUND = {name: (ID_OFFSET + 1 + i, msg) for i, (name, (_, msg)) in enumerate(METHODS.items())}
BY_ID = {v[0]: (k, v[1]) for k, v in BOUND.items()}


def frame(id_, payload=b""):
    return struct.pack("<hhi", id_, 0, len(payload)) + payload


async def handle(reader, writer):
    try:
        head = await reader.read(12)
        if head[:8] != b"DFHack?\n":
            writer.close()
            return
        writer.write(b"DFHack!\n" + struct.pack("<i", 1))
        await writer.drain()

        while True:
            try:
                h = await reader.readexactly(8)
            except asyncio.IncompleteReadError:
                return                              # client gone
            mid, _pad, size = struct.unpack("<hhi", h)
            body = await reader.readexactly(size) if size else b""

            if mid == 0:                            # BindMethod
                req = CoreBindRequest()
                req.ParseFromString(body)
                if not req.method:
                    writer.write(struct.pack("<hhi", -2, 0, 3)); await writer.drain(); continue
                info = BOUND.get(req.method)
                if info is None:
                    writer.write(struct.pack("<hhi", -2, 0, 3))   # CR_NOT_FOUND
                    await writer.drain()
                    continue
                reply = CoreBindReply(assigned_id=info[0])
                writer.write(frame(-1, reply.SerializeToString()))
            else:                                   # method call
                out = BY_ID.get(mid, (None, None))[1]
                if out is None:
                    writer.write(struct.pack("<hhi", -2, 0, 3))
                else:
                    writer.write(frame(-1, out.SerializeToString()))
            await writer.drain()
    except (ConnectionResetError, BrokenPipeError, asyncio.IncompleteReadError):
        pass
    finally:
        try:
            writer.close()
        except Exception:
            pass


async def main():
    srv = await asyncio.start_server(handle, "127.0.0.1", PORT)
    print(f"mock up on {PORT} label={LABEL} ids={sorted(v[0] for v in BOUND.values())}",
          flush=True)
    async with srv:
        await srv.serve_forever()


asyncio.run(main())
