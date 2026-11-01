"""Small hand-drawn film-edge stencils. Row-major bits, not a barcode format."""
from dataclasses import dataclass
import re


@dataclass(frozen=True)
class EdgeMarkPattern:
    width: int
    height: int
    bits: bytes

    def __post_init__(self):
        if not 1 <= self.width <= 128 or not 1 <= self.height <= 32:
            raise ValueError("Edge pattern size must be within 128 × 32 cells.")
        count = self.width * self.height
        if len(self.bits) != (count + 7) // 8 or (count % 8 and self.bits[-1] & ((1 << (8 - count % 8)) - 1)):
            raise ValueError("Invalid edge pattern bits or padding.")

    def ink(self, x: int, y: int) -> bool:
        if not 0 <= x < self.width or not 0 <= y < self.height:
            return False
        bit = y * self.width + x
        return bool(self.bits[bit // 8] & (1 << (7 - bit % 8)))

    def encode(self) -> str:
        return f"FFEM1:{self.width}x{self.height}:{self.bits.hex().upper()}"

    @classmethod
    def decode(cls, code: str) -> "EdgeMarkPattern":
        value = str(code).strip()
        if len(value) > 1045:
            raise ValueError("Edge pattern code is too large.")
        match = re.fullmatch(r"FFEM1:([1-9][0-9]{0,2})x([1-9][0-9]?):([0-9a-fA-F]+)", value)
        if match is None:
            raise ValueError("Expected an FFEM1 edge pattern code.")
        try:
            return cls(int(match[1]), int(match[2]), bytes.fromhex(match[3]))
        except ValueError as exc:
            raise ValueError("Invalid edge pattern dimensions or data.") from exc


def normalize_edge_pattern(code: str) -> str:
    return EdgeMarkPattern.decode(code).encode() if str(code).strip() else ""
