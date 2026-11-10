"""Code 128 set C for Film Foundry's own eight-digit batch/frame identifier.

Standard symbol widths (ISO/IEC 15417), cross-checked against Zint's table:
https://github.com/zint/zint/blob/master/backend/code128.c
This is not a registered DX or Kodak KEYKODE identifier.
"""
from film_foundry.contracts.edge_marks import EdgeMarkPattern

_WIDTHS=tuple('''
212222 222122 222221 121223 121322 131222 122213 122312 132212 221213
221312 231212 112232 122132 122231 113222 123122 123221 223211 221132
221231 213212 223112 312131 311222 321122 321221 312212 322112 322211
212123 212321 232121 111323 131123 131321 112313 132113 132311 211313
231113 231311 112133 112331 132131 113123 113321 133121 313121 211331
231131 213113 213311 213131 311123 311321 331121 312113 312311 332111
314111 221411 431111 111224 111422 121124 121421 141122 141221 112214
112412 122114 122411 142112 142211 241211 221114 413111 241112 134111
111242 121142 121241 114212 124112 124211 411212 421112 421211 212141
214121 412121 111143 111341 131141 114113 114311 411113 411311 113141
114131 311141 411131 211412 211214 211232
'''.split())


def code128c_symbols(digits):
    if len(digits)!=8 or not digits.isascii() or not digits.isdigit():
        raise ValueError('Film edge identifier requires eight ASCII digits')
    values=[105]+[int(digits[i:i+2]) for i in range(0,8,2)]
    check=(values[0]+sum(i*v for i,v in enumerate(values[1:],1)))%103
    return (*values,check,106)


def code128c_pattern(batch,frame_number):
    digits=f'{batch:04d}{frame_number or 0:04d}'
    values=code128c_symbols(digits)
    modules=[False]*10
    for value in values:
        for index,width in enumerate('2331112' if value==106 else _WIDTHS[value]):
            modules.extend([index%2==0]*int(width))
    modules.extend([False]*10)
    width=len(modules);height=9;bits=bytearray((width*height+7)//8)
    for y in range(height):
        for x,ink in enumerate(modules):
            if ink:
                bit=y*width+x;bits[bit//8]|=1<<(7-bit%8)
    return EdgeMarkPattern(width,height,bytes(bits))
