# GTA1 style archive (.g24 / .gry / .grx) — format notes

Verified against `STYLE001.G24` (Liberty City, 3,803,547 bytes). Everything
below is confirmed by that file unless marked otherwise.

## Identification

First 4 bytes are a little-endian version number; there is no magic string.

| value | file |
|---|---|
| 290 | `.GRX` — 8-bit editor graphics |
| 325 | `.GRY` — 8-bit in-game graphics |
| 336 | `.G24` — 24-bit in-game graphics |

## Header

No chunk directory. 16 × UInt32 LE = 64 bytes of section *sizes*, then the
sections in this order:

```
version  side_size  lid_size  aux_size  anim_size  clut_size
tile_clut_size  sprite_clut_size  new_car_clut_size  font_clut_size
palette_index_size  object_info_size  car_info_size
sprite_info_size  sprite_graphics_size  sprite_numbers_size
```

Sections: side, lid, aux, anim, clut, palette_index, object_info, car_info,
sprite_info, sprite_graphics, sprite_numbers.

**The four `*_clut_size` fields are not section sizes.** They are counts of
*virtual* palettes expressed in bytes (count × 1024). They sum to the number of
`palette_index` entries, not to `clut_size`:

```
(1581056 + 160768 + 532480 + 8192) / 1024 = 2229 = palette_index_size / 2
```

`clut_size` is the size of the *physical* palette store. Every section size is
exact except the CLUT section, which is padded — so derive it:

```
clut_section = filesize - 64 - (side+lid+aux+anim) - (everything after clut)
```

For STYLE001 that gives 1,056,768 bytes, made of **8192 bytes of font CLUTs
followed by the physical CLUT store rounded up to whole 64 KB pages**
(991232 → 1,048,576 = 16 pages). The physical store is at the *end* of the
section:

```
physical_clut_offset = clut_section_end - roundup64K(clut_size)
```

Getting this wrong by 8192 bytes yields structurally perfect tiles in
completely wrong colours, which is a confusing failure mode — the alpha byte
check below catches it.

## Tiles

64×64, 8-bit indexed. The tile area is one continuous **256-pixel-wide image**:
tiles run left to right in strips of four, then down. A tile is *not* 4096
consecutive bytes.

```
row, col = divmod(t, 4)
byte(x, y) = row*16384 + y*256 + col*64 + x
```

Four CLUTs per tile, one per remap variant, so tile virtual palettes = 4 ×
tile count (1544 = 4 × 386). Virtual palette for tile `t` remap `r` is `t*4+r`.

In `.CMP` map files, block tile bytes are 1-based and section-relative: wall
byte N is side tile N−1, lid byte N is lid tile N−1, 0 means no texture.

## Palettes

`palette_index` is a UInt16 array: virtual palette number → physical palette
number.

Physical CLUTs sit in 64 KB pages, 64 per page, in **doubleword-wide columns
down the page**. Entry `e` of physical palette `p`:

```
physical_clut_offset + (p//64)*65536 + e*256 + (p%64)*4
```

Each entry is 4 bytes **BGRA**; the 4th byte is unused and reads 0 in 99.8% of
the store. That makes a good alignment check — if your 4th bytes are not
overwhelmingly zero, your CLUT base is wrong.

Colour index 0 is always transparent.

## Sprites

`sprite_graphics` is a continuous 256-pixel-wide 8-bit bitmap. `ptr` is a byte
offset from the start of the section; rows are 256 bytes apart. Sprites are
packed at arbitrary x, so `ptr` is not row-aligned.

`sprite_info` is a **variable-length** walk, 1034 entries in STYLE001:

```
UInt8  w, h
UInt8  delta_count
UInt8  pad
UInt16 size          # always exactly w*h
UInt16 clut          # virtual palette, relative to the sprite base
UInt32 ptr           # offset into sprite_graphics
delta_entry deltas[delta_count]

delta_entry { UInt16 size; UInt32 ptr; }   # 6 bytes, ptr into sprite_graphics
```

Sprite virtual palette = `tile_clut_size/1024 + clut`. The `clut` values span
0–156 against a sprite virtual count of 157, confirming the base.

Two independent checks make this layout certain: `size == w*h` holds for every
entry, and the walk lands exactly on the end of the section (14706 bytes). A
fixed 12-byte stride does *not* work — 32 sprites carry delta tables.

## Sprite classes

`sprite_numbers` is 21 UInt16 counts whose cumulative sums give the base sprite
index of each class. They total exactly the sprite count (1034).

Order: arrow, digits, boat, box, bus, car, object, ped, speedo, tank,
traffic_lights, train, tr_doors, bike, tram, wbus, wcar, ex, tumcar, tumtruck,
ferry.

For STYLE001: arrow 49 @0, boat 1 @49, bus 6 @50, car 27 @56, object 562 @83,
ped 295 @645, tank 2 @940, traffic_lights 6 @942, train 5 @948, bike 26 @953,
wcar 7 @979, ex 48 @986.

The 32 sprites carrying deltas are exactly indices 50–82 minus one — that is,
5 of the 6 buses plus all 27 cars, which is what confirms the class bases.

## Deltas

Damage and door overlays. A delta is a stream of:

```
UInt16 offset   # relative to the last position used, initially 0
UInt8  length
UInt8  data[length]
```

**Offset is in 256-pixel page space**, not sprite space — a row step costs 256
even on a 20-pixel-wide sprite. Chunks never cross a line boundary.

Standard car delta order:

| delta | meaning |
|---|---|
| 0–3 | rear-right, rear-left, front-left, front-right dent |
| 4 | windscreen damage |
| 5–6 | left brake light, left headlight |
| 7–10 | left front door (4-frame open animation) |
| 11–14 | left back door |
| 15–18 | emergency / roof lights |
| 22–31 | mirrors of 5–14 (right side) — **not stored**, generated by mirroring |

Verified visually: applying deltas 7–10 to a car swings the door open frame by
frame, and 16–18 on the police car light up blue and red.

## car_info

38 vehicles in STYLE001. Fixed part is **174 bytes**, then `UInt16 num_doors`
at offset 172, then `num_doors` × 8-byte door records. This is the unique
solution across fixed sizes 150–230 and door strides 2–12 that walks the
section exactly.

Confirmed leading fields (Int16 each): width, height, depth, sprite_num,
weight, max_speed, min_speed, acceleration, braking, grip, handling.
`vtype` is at byte 106 and `model` at byte 107.

`sprite_num` is **relative to the vehicle's class base**, not absolute. The
vtype → class mapping falls out of the counts: vtype 0 has sprite_nums 0–5
(bus, 6) and vtype 4 has 0–26 (car, 27). vtype 3 is bike. Others (8, 13, 14)
appear once each and are unresolved.

Bytes 22–105 are the remap tables (12 HLS triples plus 12 palette bytes) and
bytes 108–171 the physics block (moment, mass, thrust, adhesion, brake bias,
turn ratio, etc.). Offsets there are inferred and not individually verified.

## object_info

102 objects. 20-byte records — three Int32 dimensions, UInt16 base_sprite,
weight, aux, UInt8 status and num_into — plus `num_into` × UInt16. Walks the
2040-byte section exactly.

## Not decoded

`anim_size` is 41 bytes in STYLE001; the animation section is passed through
by `dump` but not parsed. The 8192-byte font CLUT block ahead of the physical
palette store is located but not decoded — it does not use the same
column-wise BGRA layout.

## Sources

- DMA Design, *GBH Style Format* v4.6 (the GTA2 `.sty` spec) for the delta
  instruction stream and car delta ordering, which carry over unchanged.
- `VBGAMER45/gta1-mapeditor` README for the tile-strip and CMP indexing quirks.
- Everything else derived directly from `STYLE001.G24`.

---

# Sprite grouping and animations

`g24tool.py groups FILE -o anim/` splits the sprite set into animation groups
and writes previews. **No names come from the archive** — it contains no
strings, the spare byte in every sprite record is always 0, and the 41-byte
anim section is far too small to describe 295 ped frames. GTA1's animation
tables live in the executable. What the archive *does* give is grouping and
ordering, which is most of the work.

## How groups are found

**Stage 1 — the `clut` field.** Each sprite index entry carries a virtual
palette number. Runs of the same value are contiguous and never interleave, so
they partition each sprite class cleanly. This yields 157 groups, 25 of them in
the ped class.

**Stage 2 — cycle boundaries within a group.** A boundary is proposed where the
set of frame dimensions over the next 4 frames is disjoint from the previous 4
— a shape change that stays changed. That detection alone lands one frame early
whenever a cycle's final frame has a shape that does not recur earlier in the
same cycle, so each cut is then slid +/-3 frames to wherever the mean (w,h)
either side is most separated. Both stages are needed: detection alone split
three of the six 16-frame ped groups as 7+9 instead of 8+8.

Groups where every detected cycle has the same length (>= 4) are flagged
`uniform_cycles` in `groups.json`. Those are the confident ones.

## What it finds in STYLE001

Six ped groups of exactly 16 frames — cluts 45, 51, 52, 138, 139, 140 — each
splitting 8 + 8. The first 8 alternate between two frame shapes (ABABABAB), the
second 8 follow a different pattern. That is two 8-frame cycles per ped type,
almost certainly a walk and one other action.

The `ex` class is 4 groups of 12: a full explosion sequence (flash, fireball,
cooling smoke) in correct order, one group per mirrored quadrant. Bikes give
9+3. Traffic lights are a single run of 6.

Groups of 2–5 frames are not cycles and are left as a single run.

## Output

```
anim/
  groups.json                        every group, its clut, cycles, flags
  ped/ped_g10_clut138/
    frame_00.png .. frame_15.png     frames in order
    cycle0_8frames.apng              looping preview, one per cycle
    cycle1_8frames.apng
    sheet.png                        contact sheet, indices burned in,
                                     cycle starts marked green
```

APNG is written with stdlib zlib only (PNG plus `acTL`/`fcTL`/`fdAT` chunks,
full-canvas frames, blend SOURCE). It opens in any browser and in Pillow.

## Naming

`groups.json` carries a `name: null` field on every group, meant to be filled
in by hand or from an external table. Watching ~25 APNGs is far quicker than
inspecting 295 stills. Keep names in a separate file so they survive
re-extraction. Community sources for the actual animation names are OpenGTA's
`spriteplayer` and Carnage3D's ped animation tables.

## Caveats

`clut` groups by palette, which correlates with ped type but is not guaranteed
to coincide with animation boundaries everywhere. The uniform groups are solid;
the odd-sized ones (29, 35, 19 frames) are proposals and need an eye on the
contact sheet.

## Contact sheets for animation

`sheet.png` is a uniform grid: every cell is `max_w x max_h` for the group,
and **each frame is centred in its cell**, matching the APNG previews.

Centring matters because frame sizes vary within a group — `ped_g00_clut045`
contains 16x10, 14x18 and 16x24 frames — so top-left alignment makes the
sprite jump by up to 14px between frames when animated. It is also what the
game must do: the 12-byte sprite record is fully accounted for
(`w, h, delta_count, pad, size, clut, ptr`), the pad byte is always zero, and
there is no offset field anywhere in the archive. With no stored registration
point the engine can only centre on the sprite's own bounds.

`--sheet-no-numbers` removes the labels and all padding, giving cells of
exactly `max_w x max_h`. The grid stays uniform — padding goes to zero but
cells are not shrunk per frame, because engines index frames as `col*cell_w`.
`--sheet-background none` makes the dead space genuinely transparent rather
than filled. `--sheet-columns N` overrides the column count, which otherwise
defaults to the cycle length when a group's cycles are uniform so that each
row holds exactly one cycle.

Every group in `groups.json` carries a `sheet` block:

```json
"sheet": {
  "file": "sheet.png", "image_width": 128, "image_height": 48,
  "columns": 8, "rows": 2, "cell_width": 16, "cell_height": 24,
  "scale": 1, "numbers": false, "background": null, "frames_centred": true,
  "frames": [{"index": 0, "col": 0, "row": 0,
              "x": 2, "y": 0, "width": 12, "height": 24}]
}
```

All coordinates are in **final image pixels**, already multiplied by `scale`,
so frames can be sliced straight out of `sheet.png` with no arithmetic. The
per-frame `x/y/width/height` give the true trimmed bounds inside the cell,
which is what you need to recover a tight bounding box. Verified by slicing
all 686 frames of a two-mode run back out of the sheets and comparing them
pixel-for-pixel against the individual frame PNGs.

### One sheet per class

`--sheet-group-class` merges every group of a class into a single
`anim/<class>/sheet.png` — all 295 ped frames from all 25 clut groups in one
image — instead of one sheet per group. Frames and APNGs are still written per
group; only the sheets change. It honours `--sheet-background`,
`--sheet-no-numbers`, `--scale` and `--sheet-columns`.

Two layout consequences:

*One cell size for the whole class.* The grid has to stay uniform, so the cell
is the class-wide maximum, not the per-group maximum. A class with one large
sprite inflates every cell.

*Groups are packed continuously, not row-aligned.* A group's frames stay
contiguous in reading order but a group can begin mid-row. Padding each group
out to a whole row was tried and wastes up to 81% of the grid: `car` is 27
groups of one frame each, which would become 27 nearly empty rows. Continuous
packing leaves only trailing cells unused — measured across STYLE001, waste
drops to 0-1% for the large classes. Where every group in a class has the same
frame count the column default becomes that count, so groups end up row
aligned anyway: `ex` is 4 groups of 12 and comes out as exactly 4 rows.

`groups.json` keeps the same `sheet` block, with `scope` set to `"class"`,
`file` relative to the output directory, and `first_cell` / `first_row` /
`first_col` locating each group within the shared grid. Per-frame coordinates
remain absolute pixels in the final image, so slicing is identical either way.
Verified by round-tripping all 1034 frames out of the 12 class sheets and
comparing pixel-for-pixel against the individual frame PNGs, in numbered,
numberless-transparent and forced-column modes.

Watch the size on large classes: `object` is 562 frames of up to 104x104, so
at the default scale of 3 the merged sheet is 2592x24282. Pass `--scale 1` or
`--sheet-no-numbers`.

### Frame labels

On a numbered sheet each cell is labelled `<frame in group>/<frame in class>`:

```
0/118   1/119   2/120  ...  15/133
```

The left number is the frame's position within its animation group, drawn
green on a cycle start and grey otherwise. The right number, drawn dimmer,
counts across the whole sprite class — 0..294 for the 295 ped frames, running
through every clut group in order.

That right-hand number is the same one that suffixes the `extract` filename,
so a cell reading `6/124` is frame 6 of its group and the file
`sprites/ped/0769_124.png`. Verified against all 295 extracted ped files: zero
mismatches between the label and the `CCC` in `IIII_CCC.png`.

`groups.json` records both indices on every frame — `class_index` for the
class-relative value shown on the sheet, and `sprite_index` for the
archive-wide one (the `IIII` prefix, and the value referenced by
`sprite_index` on each vehicle in `metadata.json`).

Cells widen automatically when a label is wider than the sprite, so the
composite number never bleeds into the neighbouring cell. This makes numbered
sheets slightly wider than before — the 16-frame ped group goes from a 60px to
an 81px cell at scale 3. Numberless sheets are unaffected, so nothing changes
for animation use.

## Deltas as standalone sprites

`extract --delta-files` writes each delta on its own into
`delta_sprites/IIII_<class>/`, rather than applying it to the car. It is
independent of `--deltas`, which keeps writing the applied composites into
`deltas/`; pass both to get both sets.

**A delta both paints and erases.** A door swinging open paints the open door
and clears the body panel it swung away from, by writing colour index 0 —
which is transparent. In STYLE001, 200 of the 356 deltas erase something:
4875 of the 56709 written pixels, or 8.6%.

Alpha compositing cannot express "erase", so each delta is split into two
layers:

```
NN_name_add.png      the pixels the delta paints
NN_name_erase.png    the pixels the delta removes, flat-filled with a key
                     colour (default FF00FF, set by --delta-erase-color)
```

`--delta-erase-color` also takes `none`/`transparent`/`off`/`false`, which
punches the erased pixels out as alpha rather than colouring them. Alone that
leaves the layer blank and the tool says so, but combined with `--background`
it gives an alpha-only mask — solid everywhere except holes exactly where the
delta erases:

```
--delta-erase-color none --background 202020
```

Checked on the bus dent below: 128 transparent pixels against an otherwise
solid layer, matching the 128 marked pixels of the colour-keyed version
exactly, inverted.

To apply: clear every pixel marked in `_erase`, then composite `_add` over the
result. Verified to reproduce the `--deltas` image pixel-for-pixel for all 356
deltas — the earlier single-layer overlay only managed 156. The two layers
also account for every written pixel exactly: 51834 add + 4875 erase = 56709,
the decoder total.

A layer with no pixels is not written, so the 156 deltas that erase nothing
have no `_erase` file. `metadata.json` records what exists:

```json
"0050_bus": [
  {"delta": 2, "use": "front left dent",
   "add":   {"file": "02_front_left_dent_add.png",   "pixels": 696,
             "bounds": {"x": 29, "y": 23, "width": 14, "height": 66}},
   "erase": {"file": "02_front_left_dent_erase.png", "pixels": 128,
             "bounds": {"x": 41, "y": 23, "width":  3, "height": 66}}}
]
```

`add` or `erase` is `null` when that layer is empty. Bounds are in sprite
pixels, so both layers are full sprite size and composite at 0,0.

If you would rather apply deltas directly from the archive, `decode_delta`
returns a value array plus a mask marking every pixel the delta touches; the
mask is what distinguishes "writes transparent here" from "writes nothing
here", and the add/erase split is exactly that mask made visible.
