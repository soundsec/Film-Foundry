"""Self-authored synthetic raw sample; no camera image or external asset."""
import struct
from pathlib import Path

pack = lambda fmt, *args: struct.pack('<'+fmt, *args)
matrix = [1,0,0,0,1,0,0,0,1]
tags = {
    256: (4,pack('I',4)),257: (4,pack('I',4)),258: (3,pack('H',16)),
    259: (3,pack('H',1)),262: (3,pack('H',32803)),273: (4,pack('I',0)),
    277: (3,pack('H',1)),278: (4,pack('I',4)),279: (4,pack('I',32)),
    33421: (3,pack('HH',2,2)),33422: (1,bytes([0,1,1,2])),
    50706: (1,bytes([1,4,0,0])),50714: (5,pack('II',64,1)),50717: (4,pack('I',65535)),
    50721: (10,b''.join(pack('ii',x,1) for x in matrix)),
    50728: (5,b''.join(pack('II',round(x*1e6),1000000) for x in [.95047*.25,.25,1.08883*.25])),
}
payload_start = 8+2+len(tags)*12+4
raw_offset = payload_start+sum(len(value) for _,value in tags.values() if len(value)>4)
tags[273] = (4,pack('I',raw_offset))
payload = bytearray()
entries = bytearray()
for key,(kind,value) in sorted(tags.items()):
    size = {1:1,3:2,4:4,5:8,10:8}[kind]
    entries += pack('HHI',key,kind,len(value)//size)
    if len(value)>4:
        entries += pack('I',payload_start+len(payload))
        payload += value
    else:
        entries += value.ljust(4,b'\0')
samples = b''.join(pack('H',round(64+.5*[.95047,1,1.08883][[0,1,1,2][(y%2)*2+x%2]]*(65535-64)))
                   for y in range(4) for x in range(4))
Path(__file__).with_name('synthetic_bayer.dng').write_bytes(b'II'+pack('HIH',42,8,len(tags))+entries+pack('I',0)+payload+samples)
