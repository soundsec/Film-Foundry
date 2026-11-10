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


def factory_label_pattern(label: str, batch: int = 0) -> EdgeMarkPattern:
    """Fixed 128x9 plate: 5x7 text, quiet gap, decorative binary batch bars.

    Not DX/KeyKode or any scanner barcode. Frame numbering remains outside
    this stencil. Save the resulting FFEM1 cells, never regenerate on viewing.
    """
    from .exposure_glyphs import ROWS
    label = label.strip().upper()
    if not label or len(label)>14 or any(c not in "ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789 -" for c in label):
        raise ValueError("Use 1–14 letters, digits, spaces or hyphens")
    if isinstance(batch,bool) or not isinstance(batch,int) or not 0<=batch<=4095:
        raise ValueError("Batch must be an integer within 0–4095")
    bits=bytearray(144)
    def put(x,y):
        i=y*128+x;bits[i//8]|=1<<(7-i%8)
    for index,ch in enumerate(label):
        for y,row in enumerate(ROWS[ord(ch)]):
            for x in range(5):
                if row & (1<<(4-x)):put(2+index*6+x,1+y)
    for x in (92,94,125):
        for y in range(9):put(x,y)
    for i in range(12):
        for y in range(1,8):
            put(98+2*i,y)
            if batch & (1<<(11-i)):put(99+2*i,y)
    return EdgeMarkPattern(128,9,bytes(bits))


def factory_layout_pattern(label, batch, layout, side):
    """Declarative plate layouts; custom stencils take precedence at rendering."""
    pattern=factory_label_pattern(label,batch)
    if layout in ('legacy','combined'):return pattern
    text_only=layout=='text_only' or side=='top'
    bits=bytearray(len(pattern.bits))
    for y in range(9):
        for x in range(128):
            if pattern.ink(x,y) and ((x<90) if text_only else (x>=90)):
                i=y*128+x;bits[i//8]|=1<<(7-i%8)
    if not text_only:
        # Bars occupy the same lettering band, with space for a separate number.
        source=EdgeMarkPattern(128,9,bytes(bits));bits=bytearray(len(bits))
        for y in range(9):
            for x in range(128):
                if source.ink(92+x//4,y):
                    i=y*128+x;bits[i//8]|=1<<(7-i%8)
    return EdgeMarkPattern(128,9,bytes(bits))


def factory_edge_pattern(label,batch,frame,layout,barcode,side,seed=0):
    """Versioned old layouts stay unchanged; new plates use real symbol widths."""
    if layout in {'legacy','text_code','combined','text_only'} and barcode=='legacy':
        return factory_layout_pattern(label,batch,layout,side)
    from .edge_barcode import code128c_pattern
    lettering=factory_layout_pattern(label,batch,'text_only','top')
    bars=(code128c_pattern(batch,frame) if barcode=='code128c' else
          block_bar_pattern(batch,frame,barcode,seed) if barcode in {'blocks','staggered_blocks','paired_blocks'} else
          factory_layout_pattern(label,batch,'text_code','bottom') if barcode=='legacy' else
          EdgeMarkPattern(128,9,bytes(144)))
    if layout=='roll_markings':
        return lettering if side=='top' else factory_layout_pattern(f'BATCH {batch:04d}',batch,'text_only','bottom')
    if layout=='text_only':return lettering
    if layout=='code_text':return bars if side=='top' else lettering
    if layout in {'text_code','compact'}:return lettering if side=='top' else bars
    if layout=='cinema' and side=='top':return lettering
    if layout=='cinema':lettering=factory_layout_pattern(f'FF {batch:04d} {frame or 0:04d}',batch,'text_only','top')
    # Human-readable label and corresponding code, in separate horizontal bands.
    height=11+bars.height;bits=bytearray(128*height//8)
    for pattern,dy in ((lettering,0),(bars,11)):
        dx=(128-pattern.width)//2
        for y in range(pattern.height):
            for x in range(pattern.width):
                if pattern.ink(x,y):
                    bit=(y+dy)*128+x+dx;bits[bit//8]|=1<<(7-bit%8)
    return EdgeMarkPattern(128,height,bytes(bits))


def block_bar_pattern(batch,frame,style,seed=0):
    """Regular tracks / equal-height bars with stable decorative data.

    Two-track geometry follows the clock/data arrangement illustrated in Kodak
    US4965628, Fig. 5. Payload and guards here are our decorative design, not DX.
    https://patents.google.com/patent/US4965628A/en
    """
    width=128;height=12;bits=bytearray(width*height//8)
    state=(0x9E3779B9+int(seed)*1664525+int(batch)*1013904223+int(frame or 0)*69069)&0xffffffff
    def sample():
        nonlocal state
        state=(1664525*state+1013904223)&0xffffffff
        return state>>16
    def rectangle(x,y,w,h):
        for yy in range(y,y+h):
            for xx in range(x,x+w):
                bit=yy*width+xx;bits[bit//8]|=1<<(7-bit%8)
    if style=='blocks':
        # Fixed module pitch, two precisely aligned half-height tracks and
        # quiet margins. Only data changes; the clock rhythm never randomizes.
        data=[]
        for index in range(24):
            value=sample()&1
            if len(data)>=2 and data[-2:]==[value,value]:value=1-value
            data.append(value)
        clock=[1,1,0,1]+[1,0]*24+[1,0,1,1]
        payload=[1,0,1,1]+[value for bit in data for value in (bit,bit)]+[1,1,0,1]
        for index,(clock_bit,data_bit) in enumerate(zip(clock,payload)):
            x=8+index*2
            if clock_bit:rectangle(x,0,2,6)
            if data_bit:rectangle(x,6,2,6)
        return EdgeMarkPattern(width,height,bytes(bits))
    palettes={
        'staggered_blocks': (((0,1),(3,2),(7,1)),((0,2),(4,1),(7,2)),((0,1),(3,1),(6,2)),((0,2),(4,2),(8,1))),
        'paired_blocks': (((0,1),(3,3),(8,1)),((0,3),(5,1),(8,1)),((0,1),(3,1),(6,3)),((0,2),(4,3))),
    }
    palette=palettes[style]
    for column in range(12):
        for x,w in palette[sample()%len(palette)]:rectangle(4+column*10+x,0,w,12)
    return EdgeMarkPattern(width,height,bytes(bits))
