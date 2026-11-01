"""Packed optional finder resources, separate from the ordinary preset folder."""
import json
import zlib
from functools import lru_cache
from film_foundry.app.paths import resource_root

_MAGIC=b"FFRP\x01"
_KEY=b"FF native finder resources / 1"


def pack(payload: dict) -> bytes:
    data=zlib.compress(json.dumps(payload,ensure_ascii=False,separators=(",", ":")).encode("utf-8"),9)
    return _MAGIC+bytes(value^_KEY[i%len(_KEY)] for i,value in enumerate(data))


def unpack(data: bytes) -> dict:
    if not data.startswith(_MAGIC) or len(data)>2*1024*1024:
        raise ValueError("Invalid finder resource")
    compressed=bytes(value^_KEY[i%len(_KEY)] for i,value in enumerate(data[len(_MAGIC):]))
    decoder=zlib.decompressobj()
    raw=decoder.decompress(compressed,2*1024*1024)
    if not decoder.eof or decoder.unused_data or decoder.unconsumed_tail:
        raise ValueError("Invalid finder resource stream")
    result=json.loads(raw)
    if result.get("model")!="finder_resources_v1":
        raise ValueError("Unknown finder resource version")
    return result


def resource_path():
    return resource_root()/"film_foundry/assets/finder_resources.bin"


@lru_cache(maxsize=1)
def payload() -> dict:
    return unpack(resource_path().read_bytes())
