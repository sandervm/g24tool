#!/usr/bin/env python3
"""
g24tool - extract assets from GTA1 (1997) style archives (.g24 / .gry / .grx).

Reads the STYLE00N.G24 / .GRY files from a GTA1 install (GTADATA/) and writes
out every tile, sprite and damage overlay as PNG, plus the vehicle and object
metadata as JSON. Stdlib only - PNG and APNG are written straight through
zlib, so there is nothing to pip install.

Layout verified byte-for-byte against STYLE001.G24 (Liberty City): 386 tiles,
1034 sprites, 356 deltas, 38 vehicles, 102 objects.

  STYLE001 = Liberty City   STYLE002 = San Andreas   STYLE003 = Vice City

See README.md for usage, examples and output layout, and FORMAT.md for the
binary format itself (which parts are confirmed vs inferred).

  python3 g24tool.py info STYLE001.G24          # always start here
  python3 g24tool.py --help                     # full command reference
"""

import argparse, json, os, struct, sys, zlib

# ---------------------------------------------------------------- PNG output

def write_png(path, w, h, rgba):
    if w <= 0 or h <= 0:
        return False
    raw = bytearray()
    for y in range(h):
        raw.append(0)
        raw += rgba[y * w * 4:(y + 1) * w * 4]
    def ck(tag, data):
        return (struct.pack('>I', len(data)) + tag + data +
                struct.pack('>I', zlib.crc32(tag + data) & 0xFFFFFFFF))
    os.makedirs(os.path.dirname(path) or '.', exist_ok=True)
    with open(path, 'wb') as f:
        f.write(b'\x89PNG\r\n\x1a\n' +
                ck(b'IHDR', struct.pack('>IIBBBBB', w, h, 8, 6, 0, 0, 0)) +
                ck(b'IDAT', zlib.compress(bytes(raw), 9)) + ck(b'IEND', b''))
    return True

# -------------------------------------------------------------------- format

VERSIONS = {290: 'GRX (8-bit editor graphics)',
            325: 'GRY (8-bit in-game graphics)',
            336: 'G24 (24-bit in-game graphics)'}

FIELDS = ['version', 'side_size', 'lid_size', 'aux_size', 'anim_size',
          'clut_size', 'tile_clut_size', 'sprite_clut_size',
          'new_car_clut_size', 'font_clut_size', 'palette_index_size',
          'object_info_size', 'car_info_size', 'sprite_info_size',
          'sprite_graphics_size', 'sprite_numbers_size']
HDR = 4 * len(FIELDS)

TILE, PAGE, CLUT_PAGE, CLUT_BYTES = 64, 256, 65536, 1024

SPRITE_CLASSES = ['arrow', 'digits', 'boat', 'box', 'bus', 'car', 'object',
                  'ped', 'speedo', 'tank', 'traffic_lights', 'train',
                  'tr_doors', 'bike', 'tram', 'wbus', 'wcar', 'ex',
                  'tumcar', 'tumtruck', 'ferry']

# vtype -> sprite class, confirmed by matching each vtype's set of relative
# sprite numbers against the sprite_numbers counts.
VTYPE_CLASS = {0: 'bus', 3: 'bike', 4: 'car'}

CAR_DELTA_USE = {0: 'rear right dent', 1: 'rear left dent', 2: 'front left dent',
                 3: 'front right dent', 4: 'windscreen damage',
                 5: 'left brake light', 6: 'left headlight',
                 7: 'left front door', 8: 'left front door',
                 9: 'left front door', 10: 'left front door',
                 11: 'left back door', 12: 'left back door',
                 13: 'left back door', 14: 'left back door',
                 15: 'emerg/roof lights/decal', 16: 'emerg lights',
                 17: 'right rear emerg light', 18: 'left rear emerg light'}


class G24Error(Exception):
    pass


class Style:
    def __init__(self, data):
        self.data = data
        if len(data) < HDR:
            raise G24Error('file too small')
        self.h = dict(zip(FIELDS, struct.unpack_from('<16I', data, 0)))
        self.version = self.h['version']
        if self.version not in VERSIONS:
            raise G24Error('unrecognised version %d (expected 290/325/336)'
                           % self.version)
        if self.version != 336:
            print('warning: %s -- pixel layout should decode but the 8-bit '
                  'palette scheme differs; colours will be wrong.'
                  % VERSIONS[self.version], file=sys.stderr)
        self._layout()

    def _layout(self):
        H = self.h
        o = {}
        p = HDR
        for n, k in (('side', 'side_size'), ('lid', 'lid_size'),
                     ('aux', 'aux_size'), ('anim', 'anim_size')):
            o[n] = (p, H[k]); p += H[k]
        tail_keys = ['palette_index_size', 'object_info_size', 'car_info_size',
                     'sprite_info_size', 'sprite_graphics_size',
                     'sprite_numbers_size']
        tail = sum(H[k] for k in tail_keys)
        # The CLUT section is the only variable-padded one, so derive it.
        clut_len = len(self.data) - p - tail
        self.clut_derived = clut_len
        o['clut'] = (p, clut_len); p += clut_len
        for n, k in (('palette_index', 'palette_index_size'),
                     ('object_info', 'object_info_size'),
                     ('car_info', 'car_info_size'),
                     ('sprite_info', 'sprite_info_size'),
                     ('sprite_graphics', 'sprite_graphics_size'),
                     ('sprite_numbers', 'sprite_numbers_size')):
            o[n] = (p, H[k]); p += H[k]
        self.offsets = o
        self.total = p
        self.layout_ok = (p == len(self.data)) and clut_len >= 0
        # physical CLUTs live in whole 64K pages at the END of the section;
        # whatever precedes them is the font CLUT block.
        self.paged_len = -(-H['clut_size'] // CLUT_PAGE) * CLUT_PAGE
        self.paged_off = o['clut'][0] + clut_len - self.paged_len
        self.pre_clut_len = clut_len - self.paged_len

    def sec(self, n):
        off, size = self.offsets[n]
        return memoryview(self.data)[off:off + size]

    # tiles
    @property
    def tile_count(self):
        return (self.h['side_size'] + self.h['lid_size'] +
                self.h['aux_size']) // (TILE * TILE)

    def tile_section(self, i):
        ns = self.h['side_size'] // 4096
        nl = self.h['lid_size'] // 4096
        if i < ns: return 'side', i
        if i < ns + nl: return 'lid', i - ns
        return 'aux', i - ns - nl

    def tile_indices(self, i):
        """Tile area is one 256px-wide image; tiles run in strips of 4."""
        b = self.offsets['side'][0] + (i // 4) * (4 * TILE * TILE) + (i % 4) * TILE
        d = self.data
        return b''.join(d[b + y * PAGE:b + y * PAGE + TILE] for y in range(TILE))

    # palettes
    @property
    def palette_index(self):
        if not hasattr(self, '_pi'):
            r = self.sec('palette_index')
            self._pi = struct.unpack_from('<%dH' % (len(r) // 2), r, 0)
        return self._pi

    @property
    def virtual_counts(self):
        return {k: self.h[k + '_clut_size'] // CLUT_BYTES
                for k in ('tile', 'sprite', 'new_car', 'font')}

    def clut(self, phys):
        c = getattr(self, '_cc', None)
        if c is None: c = self._cc = {}
        if phys in c: return c[phys]
        pg, col = divmod(phys, 64)
        b = self.paged_off + pg * CLUT_PAGE + col * 4
        d = self.data
        pal = []
        for e in range(256):
            o = b + e * PAGE
            if o + 4 > len(d): pal.append((0, 0, 0, 0)); continue
            # stored BGRA; index 0 is always transparent
            pal.append((d[o + 2], d[o + 1], d[o], 0 if e == 0 else 255))
        c[phys] = pal
        return pal

    def _pal_virt(self, v):
        pi = self.palette_index
        return self.clut(pi[v] if v < len(pi) else 0)

    def tile_palette(self, i, remap=0):
        return self._pal_virt(i * 4 + remap)

    def sprite_palette(self, ent):
        return self._pal_virt(self.virtual_counts['tile'] + ent['clut'])

    # sprites
    @property
    def sprites(self):
        if hasattr(self, '_sp'): return self._sp
        r = bytes(self.sec('sprite_info'))
        gs = self.h['sprite_graphics_size']
        out, o = [], 0
        while o < len(r):
            if o + 12 > len(r):
                raise G24Error('sprite_info truncated at %d' % o)
            w, h, dc, pad = r[o], r[o + 1], r[o + 2], r[o + 3]
            size, cl = struct.unpack_from('<HH', r, o + 4)
            ptr = struct.unpack_from('<I', r, o + 8)[0]
            if size != w * h or ptr >= gs:
                raise G24Error('sprite %d inconsistent (w=%d h=%d size=%d ptr=%d)'
                               % (len(out), w, h, size, ptr))
            o += 12
            ds = [dict(zip(('size', 'ptr'), struct.unpack_from('<HI', r, o + i * 6)))
                  for i in range(dc)]
            o += dc * 6
            out.append({'w': w, 'h': h, 'clut': cl, 'ptr': ptr, 'deltas': ds})
        if o != len(r):
            raise G24Error('sprite_info walk ended at %d of %d' % (o, len(r)))
        self._sp = out
        return out

    def sprite_pixels(self, e):
        """Sprite graphics is a continuous 256px-wide bitmap."""
        b = self.offsets['sprite_graphics'][0] + e['ptr']
        d, w = self.data, e['w']
        out = bytearray(w * e['h'])
        for y in range(e['h']):
            s = b + y * PAGE
            out[y * w:(y + 1) * w] = d[s:s + w]
        return out

    def delta_stream(self, dd):
        b = self.offsets['sprite_graphics'][0] + dd['ptr']
        return bytes(self.data[b:b + dd['size']])

    # metadata
    @property
    def sprite_numbers(self):
        r = bytes(self.sec('sprite_numbers'))
        v = struct.unpack_from('<%dH' % (len(r) // 2), r, 0)
        counts, bases, c = {}, {}, 0
        for i, x in enumerate(v):
            n = SPRITE_CLASSES[i] if i < len(SPRITE_CLASSES) else 'unknown_%d' % i
            counts[n], bases[n] = x, c
            c += x
        return {'counts': counts, 'bases': bases, 'total': c}

    @property
    def cars(self):
        r = bytes(self.sec('car_info'))
        out, o = [], 0
        while o < len(r):
            if o + 174 > len(r):
                raise G24Error('car_info truncated at %d' % o)
            f = struct.unpack_from('<11h', r, o)
            vtype, model = r[o + 106], r[o + 107]
            nd = struct.unpack_from('<H', r, o + 172)[0]
            o += 174
            doors = [struct.unpack_from('<4h', r, o + i * 8) for i in range(nd)]
            o += nd * 8
            out.append({'width': f[0], 'height': f[1], 'depth': f[2],
                        'sprite_num': f[3], 'weight': f[4],
                        'max_speed': f[5], 'min_speed': f[6],
                        'acceleration': f[7], 'braking': f[8],
                        'grip': f[9], 'handling': f[10],
                        'vtype': vtype, 'model': model,
                        'sprite_class': VTYPE_CLASS.get(vtype),
                        'doors': [list(d) for d in doors]})
        if o != len(r):
            raise G24Error('car_info walk ended at %d of %d' % (o, len(r)))
        # resolve absolute sprite index where the class is known
        bases = self.sprite_numbers['bases']
        for c in out:
            cl = c['sprite_class']
            c['sprite_index'] = (bases[cl] + c['sprite_num']) if cl in bases else None
        return out

    @property
    def objects(self):
        r = bytes(self.sec('object_info'))
        out, o = [], 0
        while o + 20 <= len(r):
            w, d_, h = struct.unpack_from('<iii', r, o)
            spr, wt, aux = struct.unpack_from('<HHH', r, o + 12)
            status, ni = r[o + 18], r[o + 19]
            o += 20
            into = list(struct.unpack_from('<%dH' % ni, r, o)) if ni else []
            o += ni * 2
            out.append({'width': w, 'depth': d_, 'height': h, 'base_sprite': spr,
                        'weight': wt, 'aux': aux, 'status': status, 'into': into})
        return out, (o == len(r))

# ------------------------------------------------------------------ rendering

def to_rgba(idx, pal, bg=None):
    o = bytearray(len(idx) * 4)
    for i, v in enumerate(idx):
        r, g, b, a = pal[v]
        if a == 0 and bg is not None:
            r, g, b, a = bg
        j = i * 4
        o[j] = r; o[j + 1] = g; o[j + 2] = b; o[j + 3] = a
    return o


def decode_delta(w, h, st):
    """Stream of {UInt16 offset, UInt8 length, data}. Offset is relative to the
    last position and expressed in 256px page space, so a row step costs 256.

    Returns (values, mask). `mask` marks which pixels the delta actually
    writes, which cannot be inferred from `values` alone because a delta may
    legitimately write colour index 0."""
    vals, mask = bytearray(w * h), bytearray(w * h)
    pos, i, n = 0, 0, len(st)
    while i + 3 <= n:
        off, ln = struct.unpack_from('<HB', st, i)
        i += 3
        pos += off
        if i + ln > n:
            break
        y, x = divmod(pos, PAGE)
        for k in range(ln):
            if 0 <= x + k < w and 0 <= y < h:
                vals[y * w + x + k] = st[i + k]
                mask[y * w + x + k] = 1
        pos += ln
        i += ln
    return vals, mask


def apply_delta(idx, w, h, st):
    """Base sprite with the delta pasted over it."""
    b = bytearray(idx)
    vals, mask = decode_delta(w, h, st)
    for k in range(w * h):
        if mask[k]:
            b[k] = vals[k]
    return b


def delta_layers(w, h, st, pal, bg=None, erase_col=(255, 0, 255, 255)):
    """Split a delta into the pixels it paints and the pixels it erases.

    Deltas do both: a door swinging open paints the open door and erases the
    body panel it swung away from, by writing colour index 0. Alpha cannot
    express "erase", so the two are returned as separate RGBA layers - `add`
    carrying the painted pixels and `erase` marking the cleared ones in a flat
    key colour. Each layer also gets its bounding box and pixel count."""
    vals, mask = decode_delta(w, h, st)
    add = bytearray(w * h * 4)
    ers = bytearray(w * h * 4)
    if bg is not None:
        add[:] = bytes(bg) * (w * h)
        ers[:] = bytes(bg) * (w * h)
    ab = [w, h, -1, -1]
    eb = [w, h, -1, -1]
    n_add = n_ers = 0
    for k in range(w * h):
        if not mask[k]:
            continue
        o = k * 4
        y, x = divmod(k, w)
        if vals[k] == 0:
            ers[o], ers[o + 1], ers[o + 2], ers[o + 3] = erase_col
            n_ers += 1
            eb = [min(eb[0], x), min(eb[1], y), max(eb[2], x), max(eb[3], y)]
        else:
            r, g, b, al = pal[vals[k]]
            if al == 0 and bg is not None:
                r, g, b, al = bg
            add[o], add[o + 1], add[o + 2], add[o + 3] = r, g, b, al
            n_add += 1
            ab = [min(ab[0], x), min(ab[1], y), max(ab[2], x), max(ab[3], y)]

    def box(v):
        return None if v[2] < 0 else {'x': v[0], 'y': v[1],
                                      'width': v[2] - v[0] + 1,
                                      'height': v[3] - v[1] + 1}

    return ({'rgba': add, 'bounds': box(ab), 'pixels': n_add},
            {'rgba': ers, 'bounds': box(eb), 'pixels': n_ers})


# ------------------------------------------------------- grouping & previews

DIGITS = {'0':('111','101','101','101','111'),'1':('010','110','010','010','111'),
          '2':('111','001','111','100','111'),'3':('111','001','111','001','111'),
          '4':('101','101','111','001','001'),'5':('111','100','111','001','111'),
          '6':('111','100','111','101','111'),'7':('111','001','001','001','001'),
          '8':('111','101','111','101','111'),'9':('111','101','111','001','111'),
          '-':('000','000','111','000','000'),
          '/':('001','001','010','100','100')}


def _chunk(tag, data):
    return (struct.pack('>I', len(data)) + tag + data +
            struct.pack('>I', zlib.crc32(tag + data) & 0xFFFFFFFF))


def _idat(w, h, rgba):
    raw = bytearray()
    for y in range(h):
        raw.append(0)
        raw += rgba[y * w * 4:(y + 1) * w * 4]
    return zlib.compress(bytes(raw), 9)


def write_apng(path, w, h, frames, delay_num=1, delay_den=10):
    """APNG = PNG plus acTL/fcTL/fdAT. Full-canvas frames, blend=SOURCE."""
    if not frames:
        return False
    os.makedirs(os.path.dirname(path) or '.', exist_ok=True)
    out = [b'\x89PNG\r\n\x1a\n',
           _chunk(b'IHDR', struct.pack('>IIBBBBB', w, h, 8, 6, 0, 0, 0)),
           _chunk(b'acTL', struct.pack('>II', len(frames), 0))]
    seq = 0
    for i, f in enumerate(frames):
        out.append(_chunk(b'fcTL', struct.pack('>IIIIIHHBB', seq, w, h, 0, 0,
                                               delay_num, delay_den, 0, 0)))
        seq += 1
        data = _idat(w, h, f)
        if i == 0:
            out.append(_chunk(b'IDAT', data))
        else:
            out.append(_chunk(b'fdAT', struct.pack('>I', seq) + data))
            seq += 1
    out.append(_chunk(b'IEND', b''))
    with open(path, 'wb') as fh:
        fh.write(b''.join(out))
    return True


def draw_text(buf, W, H, x, y, text, col=(210, 210, 210)):
    for ch in str(text):
        g = DIGITS.get(ch)
        if g:
            for dy, row in enumerate(g):
                for dx in range(3):
                    if row[dx] == '1' and 0 <= x + dx < W and 0 <= y + dy < H:
                        o = ((y + dy) * W + x + dx) * 4
                        buf[o], buf[o + 1], buf[o + 2], buf[o + 3] = col + (255,)
        x += 4


def _mean(dims):
    n = len(dims) or 1
    return (sum(d[0] for d in dims) / n, sum(d[1] for d in dims) / n)


def _sep(dims, p, i, q):
    """Distance between the mean (w,h) either side of a cut at i."""
    a, b = _mean(dims[p:i]), _mean(dims[i:q])
    return ((a[0] - b[0]) ** 2 + (a[1] - b[1]) ** 2) ** 0.5


NONE_WORDS = ('none', 'transparent', 'off', 'false')


def parse_hex_color(v, what='colour', allow_none=False):
    """RRGGBB or RRGGBBAA, optional leading #. With allow_none, also accepts
    none/transparent/off/false and returns fully transparent black."""
    t = str(v).strip().lstrip('#')
    if allow_none and t.lower() in NONE_WORDS:
        return (0, 0, 0, 0)
    if len(t) in (6, 8) and all(c in '0123456789abcdefABCDEF' for c in t):
        a = int(t[6:8], 16) if len(t) == 8 else 255
        return (int(t[0:2], 16), int(t[2:4], 16), int(t[4:6], 16), a)
    raise G24Error('bad %s %r: want RRGGBB, RRGGBBAA%s'
                   % (what, v, ' or none' if allow_none else ''))


def text_width(t):
    return max(0, 4 * len(str(t)) - 1)


def draw_label(buf, W, H, x, y, text, col_a, col_b):
    """Draw `a/b`, the part before the slash in col_a and the rest in col_b."""
    i = text.find('/')
    head, tail = (text, '') if i < 0 else (text[:i], text[i:])
    draw_text(buf, W, H, x, y, head, col_a)
    if tail:
        draw_text(buf, W, H, x + 4 * len(head), y, tail, col_b)


def parse_sheet_bg(v):
    """HEX (RRGGBB / RRGGBBAA, optional #) or none/transparent/off/false."""
    if v is None:
        return (32, 32, 36, 255)
    return parse_hex_color(v, '--sheet-background', allow_none=True)


def split_cycles(dims, win=4, minlen=3, refine=3):
    """Two-stage. Detect that a boundary exists where the set of frame
    dimensions over the next `win` frames is disjoint from the previous `win`,
    then slide the cut +/-`refine` to wherever the mean (w,h) either side is
    most separated. Detection alone lands one frame early when a cycle's last
    frame has a shape that does not recur earlier in the cycle."""
    n = len(dims)
    if n < 2 * minlen:
        return [(0, n)]
    cand = []
    i = minlen
    while i <= n - minlen:
        a = set(dims[max(cand[-1] if cand else 0, i - win):i])
        b = set(dims[i:i + win])
        if a and b and a.isdisjoint(b):
            cand.append(i)
            i += minlen
        else:
            i += 1
    cuts = []
    for k, c in enumerate(cand):
        prev = cuts[-1] if cuts else 0
        nxt = cand[k + 1] if k + 1 < len(cand) else n
        lo, hi = max(prev + minlen, c - refine), min(nxt - minlen, c + refine)
        if lo > hi:
            cuts.append(c)
            continue
        cuts.append(max(range(lo, hi + 1), key=lambda j: _sep(dims, prev, j, nxt)))
    bounds = [0] + cuts + [n]
    return [(bounds[k], bounds[k + 1]) for k in range(len(bounds) - 1)
            if bounds[k + 1] > bounds[k]]


def sprite_groups(st):
    """Contiguous runs of equal `clut` within each sprite class."""
    sn = st.sprite_numbers
    sprites = st.sprites
    groups = []
    for cls, cnt in sn['counts'].items():
        if not cnt:
            continue
        base = sn['bases'][cls]
        runs = []
        for i in range(cnt):
            e = sprites[base + i]
            if runs and runs[-1]['clut'] == e['clut']:
                runs[-1]['end'] = i
            else:
                runs.append({'clut': e['clut'], 'start': i, 'end': i})
        for gi, r in enumerate(runs):
            n = r['end'] - r['start'] + 1
            dims = [(sprites[base + r['start'] + k]['w'],
                     sprites[base + r['start'] + k]['h']) for k in range(n)]
            cyc = split_cycles(dims)
            lens = {b - a for a, b in cyc}
            clean = len(cyc) > 1 and len(lens) == 1 and min(lens) >= 4
            groups.append({
                'id': '%s_g%02d_clut%03d' % (cls, gi, r['clut']),
                'sprite_class': cls, 'clut': r['clut'],
                'first_index': base + r['start'], 'count': n,
                'class_range': [r['start'], r['end']],
                'cycles': [{'start': a, 'length': b - a} for a, b in cyc],
                'uniform_cycles': clean,
                'name': None,
            })
    return groups

# ------------------------------------------------------------------- commands

def load(p):
    with open(p, 'rb') as f:
        return Style(f.read())


def cmd_info(a):
    st = load(a.file)
    H = st.h
    print('file     : %s (%d bytes)' % (a.file, len(st.data)))
    print('version  : %d  %s\n' % (st.version, VERSIONS[st.version]))
    print('-- header ' + '-' * 46)
    for k in FIELDS[1:]:
        print('  %-22s %10d' % (k, H[k]))
    print('\n-- sections ' + '-' * 44)
    for n in ('side', 'lid', 'aux', 'anim', 'clut', 'palette_index',
              'object_info', 'car_info', 'sprite_info', 'sprite_graphics',
              'sprite_numbers'):
        off, size = st.offsets[n]
        print('  %-16s @%9d  %9d' % (n, off, size))
    ok = lambda b: 'OK  ' if b else 'FAIL'
    print('\n-- checks ' + '-' * 46)
    print('  [%s] section table matches file size (%d vs %d)'
          % (ok(st.layout_ok), st.total, len(st.data)))
    vc = st.virtual_counts
    tot = sum(vc.values())
    pin = len(st.palette_index)
    print('  [%s] virtual palettes == palette_index entries (%d vs %d)'
          % (ok(tot == pin), tot, pin))
    print('        tile %d, sprite %d, new_car %d, font %d'
          % (vc['tile'], vc['sprite'], vc['new_car'], vc['font']))
    print('  [%s] tile virtual palettes == 4 x tiles (%d vs %d)'
          % (ok(vc['tile'] == 4 * st.tile_count), vc['tile'], 4 * st.tile_count))
    print('  [%s] physical CLUT area is whole 64K pages (%d = %d x 64K)'
          % (ok(st.paged_len % CLUT_PAGE == 0), st.paged_len,
             st.paged_len // CLUT_PAGE))
    print('        leading block before it: %d bytes (font_clut_size = %d)'
          % (st.pre_clut_len, H['font_clut_size']))
    print('        max physical palette referenced: %d' % max(st.palette_index))
    print('        tiles: %d (%d side, %d lid, %d aux)'
          % (st.tile_count, H['side_size'] // 4096, H['lid_size'] // 4096,
             H['aux_size'] // 4096))
    try:
        sp = st.sprites
        nd = sum(1 for e in sp if e['deltas'])
        print('  [OK  ] sprite_info walked exactly: %d sprites, %d with deltas'
              % (len(sp), nd))
    except G24Error as e:
        print('  [FAIL] sprite_info: %s' % e); sp = []
    sn = st.sprite_numbers
    print('  [%s] sprite_numbers total == sprite count (%d vs %d)'
          % (ok(sn['total'] == len(sp)), sn['total'], len(sp)))
    try:
        cars = st.cars
        print('  [OK  ] car_info walked exactly: %d vehicles' % len(cars))
    except G24Error as e:
        print('  [FAIL] car_info: %s' % e)
    objs, oook = st.objects
    print('  [%s] object_info walked exactly: %d objects' % (ok(oook), len(objs)))
    print('\n-- sprite classes ' + '-' * 38)
    for k, v in sn['counts'].items():
        if v: print('  %-16s %5d  base %5d' % (k, v, sn['bases'][k]))


def cmd_extract(a):
    st = load(a.file)
    if not st.layout_ok:
        print('WARNING: section table does not match file size; run "info".',
              file=sys.stderr)
    bg = parse_hex_color(a.background, '--background') if a.background else None
    erase_col = parse_hex_color(a.delta_erase_color, '--delta-erase-color',
                                allow_none=True)
    if a.delta_files and erase_col[3] == 0 and bg is None:
        print('note: --delta-erase-color is transparent and --background is '
              'unset, so the _erase layers will be entirely blank. Pair it '
              'with --background to get an alpha-only mask.', file=sys.stderr)
    out = a.out
    n_t = n_s = n_d = n_o = 0
    delta_meta = {}

    if not a.no_tiles:
        for i in range(st.tile_count):
            sect, loc = st.tile_section(i)
            idx = st.tile_indices(i)
            for rm in range(a.remaps):
                nm = '%04d.png' % loc if rm == 0 else '%04d_remap%d.png' % (loc, rm)
                write_png(os.path.join(out, 'tiles', sect, nm), TILE, TILE,
                          to_rgba(idx, st.tile_palette(i, rm), bg))
                n_t += 1
        print('tiles   : %d PNGs' % n_t)

    sprites = []
    if not a.no_sprites:
        try:
            sprites = st.sprites
        except G24Error as e:
            print('sprites : FAILED (%s)' % e, file=sys.stderr)
        cls = {}
        if sprites:
            sn = st.sprite_numbers
            for name, c in sn['counts'].items():
                for k in range(c):
                    cls[sn['bases'][name] + k] = (name, k)
        for i, e in enumerate(sprites):
            idx = st.sprite_pixels(e)
            pal = st.sprite_palette(e)
            name, k = cls.get(i, ('sprite', i))
            sub = os.path.join(out, 'sprites', name)
            write_png(os.path.join(sub, '%04d_%03d.png' % (i, k)),
                      e['w'], e['h'], to_rgba(idx, pal, bg))
            n_s += 1
            if (a.deltas or a.delta_files) and e['deltas']:
                boxes = []
                for j, dd in enumerate(e['deltas']):
                    s = st.delta_stream(dd)
                    if not s:
                        continue
                    lbl = CAR_DELTA_USE.get(j, 'delta')
                    fn = '%02d_%s.png' % (j, ''.join(c if c.isalnum() else '_'
                                                     for c in lbl))
                    if a.deltas:
                        m = apply_delta(idx, e['w'], e['h'], s)
                        write_png(os.path.join(out, 'deltas',
                                               '%04d_%s' % (i, name), fn),
                                  e['w'], e['h'], to_rgba(m, pal, bg))
                        n_d += 1
                    if a.delta_files:
                        add, ers = delta_layers(e['w'], e['h'], s, pal, bg,
                                                erase_col)
                        ddir = os.path.join(out, 'delta_sprites',
                                            '%04d_%s' % (i, name))
                        rec = {'delta': j, 'use': lbl}
                        for tag, layer in (('add', add), ('erase', ers)):
                            if not layer['pixels']:
                                rec[tag] = None
                                continue
                            f2 = fn[:-4] + '_%s.png' % tag
                            write_png(os.path.join(ddir, f2),
                                      e['w'], e['h'], layer['rgba'])
                            n_o += 1
                            rec[tag] = {'file': f2, 'pixels': layer['pixels'],
                                        'bounds': layer['bounds']}
                        boxes.append(rec)
                if a.delta_files and boxes:
                    delta_meta['%04d_%s' % (i, name)] = boxes
        bits = []
        if a.deltas:
            bits.append('%d applied' % n_d)
        if a.delta_files:
            bits.append('%d add/erase' % n_o)
        print('sprites : %d PNGs%s'
              % (n_s, (' (%s delta PNGs)' % ', '.join(bits)) if bits else ''))

    if a.palettes:
        n = st.paged_len // CLUT_BYTES
        rows = bytearray()
        for p in range(n):
            for r, g, b, _ in st.clut(p):
                rows += bytes((r, g, b, 255))
        write_png(os.path.join(out, 'palettes.png'), 256, n, rows)
        print('palettes: %d CLUTs' % n)

    meta = {'source': os.path.basename(a.file), 'version': st.version,
            'header': st.h,
            'sections': {k: {'offset': v[0], 'size': v[1]}
                         for k, v in st.offsets.items()},
            'physical_clut_offset': st.paged_off,
            'tile_counts': {'side': st.h['side_size'] // 4096,
                            'lid': st.h['lid_size'] // 4096,
                            'aux': st.h['aux_size'] // 4096},
            'sprite_numbers': st.sprite_numbers}
    for key, fn in (('cars', lambda: st.cars),
                    ('objects', lambda: st.objects[0])):
        try: meta[key] = fn()
        except Exception as e: meta[key + '_error'] = str(e)
    if delta_meta:
        meta['delta_sprites'] = delta_meta
    if sprites:
        meta['sprites'] = [{'index': i, 'w': e['w'], 'h': e['h'],
                            'clut': e['clut'], 'ptr': e['ptr'],
                            'delta_count': len(e['deltas'])}
                           for i, e in enumerate(sprites)]
    os.makedirs(out, exist_ok=True)
    with open(os.path.join(out, 'metadata.json'), 'w') as f:
        json.dump(meta, f, indent=2)
    print('meta    : metadata.json')


def build_sheet(cells, cols, cw, ch, numbers, bg, scale,
                labels=None, marks=frozenset()):
    """Uniform grid of `cells`, each (w, h, rgba) or None for a blank slot.
    Frames are centred in their cell. Returns the scaled buffer plus geometry
    in final image pixels."""
    pad, label = (2, 8) if numbers else (0, 0)
    lw = max((text_width(l) for l in (labels or []) if l), default=0) if numbers else 0
    cellw = max(cw + 2 * pad, lw + 2 * pad)
    cellh = ch + pad + label
    inner = cellw - 2 * pad
    rows = max(1, (len(cells) + cols - 1) // cols)
    W, H = cols * cellw, rows * cellh
    buf = bytearray(bytes(bg) * (W * H))
    pos = []
    for k, c in enumerate(cells):
        ci, ri = k % cols, k // cols
        tx, ty = ci * cellw, ri * cellh
        if c is None:
            pos.append(None)
            continue
        w, h, px = c
        ox, oy = tx + pad + (inner - w) // 2, ty + pad + (ch - h) // 2
        for y in range(h):
            for x in range(w):
                q = (y * w + x) * 4
                if px[q + 3] == 0:
                    continue
                o = ((oy + y) * W + ox + x) * 4
                buf[o:o + 4] = px[q:q + 4]
        if numbers and labels and labels[k] is not None:
            draw_label(buf, W, H, tx + pad, ty + ch + 3, labels[k],
                       (120, 220, 120) if k in marks else (150, 150, 155),
                       (105, 105, 112))
        pos.append({'col': ci, 'row': ri, 'x': ox * scale, 'y': oy * scale,
                    'width': w * scale, 'height': h * scale})
    if scale > 1:
        big = bytearray(W * scale * H * scale * 4)
        for y in range(H):
            row = bytearray()
            for x in range(W):
                row += buf[(y * W + x) * 4:(y * W + x) * 4 + 4] * scale
            for k in range(scale):
                o = ((y * scale + k) * W * scale) * 4
                big[o:o + len(row)] = row
        buf = big
    return {'buf': buf, 'W': W * scale, 'H': H * scale,
            'cell_width': cellw * scale, 'cell_height': cellh * scale,
            'rows': rows, 'columns': cols, 'pos': pos}


def cmd_groups(a):
    st = load(a.file)
    groups = sprite_groups(st)
    only = set(a.classes.split(',')) if a.classes else None
    if only:
        groups = [g for g in groups if g['sprite_class'] in only]
    sprites = st.sprites
    numbers = not a.sheet_no_numbers
    bg = parse_sheet_bg(a.sheet_background)
    sc = max(1, a.scale if a.scale is not None else (3 if numbers else 1))
    bg_name = None if bg[3] == 0 else '#%02X%02X%02X' % bg[:3]

    store = {}
    n_f = n_a = 0
    for g in groups:
        gdir = os.path.join(a.out, g['sprite_class'], g['id'])
        rendered = []
        for k in range(g['count']):
            e = sprites[g['first_index'] + k]
            rgba = to_rgba(st.sprite_pixels(e), st.sprite_palette(e))
            rendered.append((e['w'], e['h'], rgba))
            write_png(os.path.join(gdir, 'frame_%02d.png' % k), e['w'], e['h'], rgba)
            n_f += 1
        store[g['id']] = rendered
        cw = max(w for w, _, _ in rendered)
        ch = max(h for _, h, _ in rendered)
        for ci, cyc in enumerate(g['cycles']):
            if cyc['length'] < 2:
                continue
            frames = []
            for k in range(cyc['start'], cyc['start'] + cyc['length']):
                w, h, px = rendered[k]
                canvas = bytearray(cw * ch * 4)
                ox, oy = (cw - w) // 2, (ch - h) // 2
                for y in range(h):
                    o = ((oy + y) * cw + ox) * 4
                    canvas[o:o + w * 4] = px[y * w * 4:(y + 1) * w * 4]
                frames.append(canvas)
            write_apng(os.path.join(gdir, 'cycle%d_%dframes.apng' % (ci, len(frames))),
                       cw, ch, frames, 1, a.fps)
            n_a += 1

    n_sheets = 0
    if a.sheet_group_class:
        byclass = {}
        for g in groups:
            byclass.setdefault(g['sprite_class'], []).append(g)
        for cls, gl in byclass.items():
            # one cell size for the whole class, else the grid is not uniform
            cw = max(w for g in gl for w, _, _ in store[g['id']])
            ch = max(h for g in gl for _, h, _ in store[g['id']])
            counts = {g['count'] for g in gl}
            if a.sheet_columns:
                cols = max(1, a.sheet_columns)
            elif len(counts) == 1 and gl[0]['count'] >= 2:
                cols = gl[0]['count']        # every group is one row
            else:
                cols = 8
            # packed continuously: a group's frames stay contiguous in reading
            # order but are not row-aligned. Padding each group out to a whole
            # row wastes up to 81% of the grid on classes full of small groups
            # (27 single-frame cars would become 27 rows).
            cells, labels, marks, spans = [], [], set(), {}
            for g in gl:
                spans[g['id']] = len(cells)
                cyc = {c['start'] for c in g['cycles']}
                for k, f in enumerate(store[g['id']]):
                    if k in cyc:
                        marks.add(len(cells))
                    cells.append(f)
                    labels.append('%d/%d' % (k, g['class_range'][0] + k))
            sh = build_sheet(cells, cols, cw, ch, numbers, bg, sc, labels, marks)
            write_png(os.path.join(a.out, cls, 'sheet.png'),
                      sh['W'], sh['H'], sh['buf'])
            n_sheets += 1
            for g in gl:
                s0 = spans[g['id']]
                fr = []
                for k in range(g['count']):
                    q = dict(sh['pos'][s0 + k])
                    q['index'] = k
                    q['class_index'] = g['class_range'][0] + k
                    q['sprite_index'] = g['first_index'] + k
                    fr.append(q)
                g['sheet'] = {
                    'file': '%s/sheet.png' % cls, 'scope': 'class',
                    'image_width': sh['W'], 'image_height': sh['H'],
                    'columns': sh['columns'], 'rows': sh['rows'],
                    'cell_width': sh['cell_width'],
                    'cell_height': sh['cell_height'],
                    'first_cell': s0, 'first_row': s0 // sh['columns'],
                    'first_col': s0 % sh['columns'],
                    'scale': sc, 'numbers': numbers, 'background': bg_name,
                    'frames_centred': True, 'frames': fr,
                }
    else:
        for g in groups:
            rendered = store[g['id']]
            cw = max(w for w, _, _ in rendered)
            ch = max(h for _, h, _ in rendered)
            lens = {c['length'] for c in g['cycles']}
            if a.sheet_columns:
                cols = max(1, min(a.sheet_columns, g['count']))
            elif len(lens) == 1:
                cols = g['cycles'][0]['length']
            else:
                cols = min(8, g['count'])
            marks = {c['start'] for c in g['cycles']}
            lab = ['%d/%d' % (k, g['class_range'][0] + k)
                   for k in range(g['count'])]
            sh = build_sheet(rendered, cols, cw, ch, numbers, bg, sc, lab, marks)
            write_png(os.path.join(a.out, g['sprite_class'], g['id'], 'sheet.png'),
                      sh['W'], sh['H'], sh['buf'])
            n_sheets += 1
            fr = []
            for k in range(g['count']):
                q = dict(sh['pos'][k])
                q['index'] = k
                q['class_index'] = g['class_range'][0] + k
                q['sprite_index'] = g['first_index'] + k
                fr.append(q)
            g['sheet'] = {
                'file': 'sheet.png', 'scope': 'group',
                'image_width': sh['W'], 'image_height': sh['H'],
                'columns': sh['columns'], 'rows': sh['rows'],
                'cell_width': sh['cell_width'], 'cell_height': sh['cell_height'],
                'first_cell': 0, 'first_row': 0, 'first_col': 0,
                'scale': sc, 'numbers': numbers, 'background': bg_name,
                'frames_centred': True, 'frames': fr,
            }

    meta = {'source': os.path.basename(a.file), 'groups': groups}
    os.makedirs(a.out, exist_ok=True)
    with open(os.path.join(a.out, 'groups.json'), 'w') as f:
        json.dump(meta, f, indent=2)
    uni = sum(1 for g in groups if g['uniform_cycles'])
    print('groups  : %d (%d with uniform cycles)' % (len(groups), uni))
    print('frames  : %d PNGs' % n_f)
    print('previews: %d APNGs + %d %s sheet%s'
          % (n_a, n_sheets, 'class' if a.sheet_group_class else 'group',
             '' if n_sheets == 1 else 's'))
    print('index   : groups.json')


def cmd_dump(a):
    st = load(a.file)
    os.makedirs(a.out, exist_ok=True)
    for n, (off, size) in st.offsets.items():
        with open(os.path.join(a.out, n + '.bin'), 'wb') as f:
            f.write(st.data[off:off + size])
        print('%-16s %10d' % (n, size))


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__,
            formatter_class=argparse.RawDescriptionHelpFormatter)
    s = ap.add_subparsers(dest='cmd', required=True)
    p = s.add_parser('info', help='parse header and run consistency checks')
    p.add_argument('file'); p.set_defaults(func=cmd_info)

    p = s.add_parser('extract', help='write tiles, sprites, deltas, metadata')
    p.add_argument('file')
    p.add_argument('-o', '--out', default='out', help='output dir (default: out)')
    p.add_argument('--no-tiles', action='store_true', help='skip map tiles')
    p.add_argument('--no-sprites', action='store_true', help='skip sprites')
    p.add_argument('--deltas', action='store_true',
                   help='write each damage/door overlay applied to the sprite')
    p.add_argument('--delta-files', action='store_true',
                   help='write each delta as standalone _add / _erase overlay '
                        'PNGs instead of applying it')
    p.add_argument('--delta-erase-color', default='FF00FF', metavar='HEX|none',
                   help='key colour for erased pixels in the _erase layer, or '
                        'none/transparent/off/false to punch them out as alpha '
                        '(default: FF00FF)')
    p.add_argument('--palettes', action='store_true', help='write palettes.png')
    p.add_argument('--remaps', type=int, default=1,
                   help='tile remap variants to write, 1-4 (default: 1)')
    p.add_argument('--background', default=None,
                   help='hex fill for transparent pixels, e.g. FF00FF')
    p.set_defaults(func=cmd_extract)
    p = s.add_parser('groups', help='group sprites into animations + previews')
    p.add_argument('file')
    p.add_argument('-o', '--out', default='anim', help='output dir (default: anim)')
    p.add_argument('--classes', default=None,
                   help='comma-separated classes, e.g. ped,car (default: all)')
    p.add_argument('--fps', type=int, default=10,
                   help='preview playback rate (default: 10)')
    p.add_argument('--scale', type=int, default=None,
                   help='contact sheet zoom (default: 3, or 1 with '
                        '--sheet-no-numbers)')
    p.add_argument('--sheet-background', default=None, metavar='HEX|none',
                   help='sheet background: RRGGBB, RRGGBBAA, or none for '
                        'transparent (default: 202024)')
    p.add_argument('--sheet-no-numbers', action='store_true',
                   help='omit frame numbers and all padding, giving a tight '
                        'uniform grid for use as an animation sheet')
    p.add_argument('--sheet-group-class', action='store_true',
                   help='one merged sheet per class instead of one per group')
    p.add_argument('--sheet-columns', type=int, default=None, metavar='N',
                   help='force sheet columns (default: cycle length when a '
                        "group's cycles are uniform, else 8)")
    p.set_defaults(func=cmd_groups)

    p = s.add_parser('dump', help='write each raw section to a .bin file')
    p.add_argument('file')
    p.add_argument('-o', '--out', default='sections',
                   help='output dir (default: sections)')
    p.set_defaults(func=cmd_dump)
    a = ap.parse_args(argv)
    try:
        a.func(a)
    except G24Error as e:
        print('error: %s' % e, file=sys.stderr)
        return 1
    except BrokenPipeError:
        # piping into head/less closes stdout early; not an error
        try:
            sys.stdout.close()
        except Exception:
            pass
        os._exit(0)
    return 0


if __name__ == '__main__':
    sys.exit(main())
