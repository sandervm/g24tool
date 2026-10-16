# g24tool

Extract assets from GTA1 (1997) style archives — `.g24` / `.gry` / `.grx`.

`g24tool` reads the `STYLE00N.G24` / `.GRY` files from a GTA1 install (in `GTADATA/`) and writes out every tile, sprite and damage overlay as PNG, plus the vehicle and object metadata as JSON. It's a single Python file using **only the standard library** — PNG and APNG are written straight through `zlib`, so there is nothing to `pip install`.

The layout was verified byte-for-byte against `STYLE001.G24` (Liberty City): **386 tiles, 1034 sprites, 356 deltas, 38 vehicles, 102 objects**.

> The three cities, for reference:
>
> | File | City |
> |---|---|
> | `STYLE001` | Liberty City |
> | `STYLE002` | San Andreas |
> | `STYLE003` | Vice City |

## Table of contents

- [Requirements](#requirements)
- [Quick start](#quick-start)
- [Commands](#commands)
  - [`info`](#info)
  - [`extract`](#extract)
  - [`groups`](#groups)
  - [`dump`](#dump)
- [Examples](#examples)
- [Output layout](#output-layout)
- [Animations](#animations)
- [Notes](#notes)
- [File format](#file-format)
- [License](#license)

## Requirements

- Python 3 (standard library only — no dependencies)

## Quick start

```bash
# always start here
python3 g24tool.py info STYLE001.G24

# everything, including damage overlays and the palette dump
python3 g24tool.py extract STYLE001.G24 -o out/ --deltas --palettes
```

## Commands

### `info`

```
info FILE
```

Parse the header and run eight consistency checks. Costs nothing and tells you immediately whether the rest will decode. **Run this first on any archive you have not tried before.** It also prints the tile counts and the sprite class table (base index and count for cars, peds, objects, …).

### `extract`

```
extract FILE [-o DIR]
```

Write the artwork and `metadata.json`.

| Option | Description |
|---|---|
| `-o, --out DIR` | Output directory (default: `out`) |
| `--no-tiles` | Skip the 64×64 map tiles |
| `--no-sprites` | Skip sprites |
| `--deltas` | Write each car sprite with each damage/door overlay applied, named by function, into `deltas/` |
| `--delta-files` | Write each delta as standalone overlay PNGs into `delta_sprites/`, rather than applying it to the car. Independent of `--deltas`; pass both to get both sets |
| `--delta-erase-color HEX\|none` | Key colour for the `_erase` layer, `RRGGBB` or `RRGGBBAA` (default: `FF00FF`). Also accepts `none`/`transparent`/`off`/`false`, which punches the erased pixels out as alpha instead of colouring them |
| `--palettes` | Write `palettes.png`, one CLUT per row |
| `--remaps N` | Tile remap variants to write, 1–4 (default 1). Every tile stores four palette variants; the map picks per block. 4 quadruples the tile output |
| `--background HEX` | Fill transparent pixels with a colour instead of alpha, e.g. `--background FF00FF` for magenta. Useful for engines that key on a colour rather than alpha |

**Deltas as standalone sprites.** A delta both paints and erases — a door swinging open paints the open door and clears the body panel it swung away from, by writing colour index 0. Alpha cannot express "erase", so each delta is split into two files:

```
NN_name_add.png     pixels the delta paints
NN_name_erase.png   pixels the delta removes, flat-filled with the key colour
```

Apply them as: clear every pixel marked in `_erase`, then composite `_add` over the result. That reproduces the `--deltas` image exactly for all 356 deltas in STYLE001.

`--delta-erase-color` also accepts `none`/`transparent`/`off`/`false`, which punches the erased pixels out as alpha instead of colouring them. On its own that leaves the layer entirely blank and the tool says so; paired with `--background` it gives an alpha-only mask — the layer solid everywhere except holes exactly where the delta erases:

```bash
--delta-erase-color none --background 202020
```

A layer with no pixels is not written, so the 156 deltas that erase nothing have no `_erase` file. Which files exist, their pixel counts and their bounding boxes are all in `metadata.json` under `delta_sprites`.
### `groups`

```
groups FILE [-o DIR]
```

Split the sprites into animation groups and write looping previews. Sprites are grouped by runs of their palette (clut) field, then cut into cycles where the frame shape changes. See [Animations](#animations) below.

| Option | Description |
|---|---|
| `-o, --out DIR` | Output directory (default: `anim`) |
| `--classes LIST` | Comma-separated classes to process, e.g. `ped,car,ex`. Valid: `arrow digits boat box bus car object ped speedo tank traffic_lights train tr_doors bike tram wbus wcar ex tumcar tumtruck ferry` (default: every class present in the file) |
| `--fps N` | APNG playback rate (default: 10) |

**Contact sheet options:**

| Option | Description |
|---|---|
| `--scale N` | Zoom, nearest neighbour. Default 3 for a sheet with numbers (sprites are 8–32px, so 1 is unreadable), or 1 with `--sheet-no-numbers`. Explicit wins either way |
| `--sheet-background HEX\|none` | `RRGGBB`, `RRGGBBAA`, or `none`/`transparent`/`off`/`false` for a real alpha background (default: `202024`). Distinct from `extract --background`, which fills colour index 0 inside each sprite; this paints only the sheet's dead space between frames |
| `--sheet-no-numbers` | Drop the frame numbers and all padding, giving a tight uniform grid suitable for feeding to an engine as an animation sheet. Also flips `--scale` to 1 |
| `--sheet-group-class` | One merged sheet per class instead of one per group |
| `--sheet-columns N` | Force the column count. Default is the cycle length when a group's cycles are all the same length, so each row holds exactly one cycle, else 8 |

Labels read `<frame in group>/<frame in class>`, e.g. `6/124`. The left number is the frame's position in its animation group and is green on a cycle start; the right number, drawn dimmer, counts across the whole class — 0..294 for the 295 ped frames, running through every clut group in order. That is the same number that suffixes the extract filename, so `6/124` is frame 6 of its group and `sprites/ped/0769_124.png`. `groups.json` records it per frame as `class_index`, alongside `sprite_index` for the archive-wide value. Cells widen when needed so the label always fits.

Frames are centred in their cell, matching the APNG previews. Frame sizes vary within a group (one ped group holds 16×10, 14×18 and 16×24 frames), so top-left alignment would make the sprite jump by up to 14px between frames when animated. The sprite record carries no offset field and its spare byte is always zero, so centring is the only registration the game itself could have used.

"Tight" still means a uniform grid: the padding goes to zero, but every cell stays `max_w × max_h`, because engines index frames as `col*cell_w`. Per-cell geometry is written to `groups.json` (see below) so nothing has to be measured off the image.

### `dump`

```
dump FILE [-o DIR]
```

Write each of the eleven raw sections to its own `.bin`. For poking at the parts this tool does not decode — the anim section and the font CLUT block. `-o, --out DIR` (default: `sections`).

## Examples

```bash
# always start here
python3 g24tool.py info STYLE001.G24

# everything, including damage overlays and the palette dump
python3 g24tool.py extract STYLE001.G24 -o out/ --deltas --palettes

# tiles only, all four remap variants, magenta instead of alpha
python3 g24tool.py extract STYLE001.G24 -o tiles/ --no-sprites \
        --remaps 4 --background FF00FF

# just the vehicle and object metadata, no image files
python3 g24tool.py extract STYLE001.G24 -o meta/ --no-tiles --no-sprites

# ped animations, big readable sheets, slower playback
python3 g24tool.py groups STYLE001.G24 -o anim/ --classes ped --scale 4 --fps 8

# explosions and vehicles
python3 g24tool.py groups STYLE001.G24 -o anim/ --classes ex,car,bus

# deltas as standalone add/erase overlays rather than applied to the car
python3 g24tool.py extract STYLE001.G24 -o out/ --delta-files

# same, marking erased pixels in green instead of magenta
python3 g24tool.py extract STYLE001.G24 -o out/ --delta-files \
        --delta-erase-color 00FF00

# erase layers as an alpha-only mask: solid, with holes where it erases
python3 g24tool.py extract STYLE001.G24 -o out/ --delta-files \
        --delta-erase-color none --background 202020

# both: applied composites and the raw overlays
python3 g24tool.py extract STYLE001.G24 -o out/ --deltas --delta-files

# animation-ready sheets: transparent, no numbers, no padding, 1:1
python3 g24tool.py groups STYLE001.G24 -o sheets/ \
        --sheet-no-numbers --sheet-background none

# same, but 4 frames per row and a magenta key colour instead of alpha
python3 g24tool.py groups STYLE001.G24 -o sheets/ --classes ped \
        --sheet-no-numbers --sheet-background FF00FF --sheet-columns 4

# one merged sheet per class, ready to load as an atlas
python3 g24tool.py groups STYLE001.G24 -o atlas/ --sheet-group-class \
        --sheet-no-numbers --sheet-background none

# all 295 ped frames on one readable numbered sheet, 16 per row
python3 g24tool.py groups STYLE001.G24 -o atlas/ --classes ped \
        --sheet-group-class --sheet-columns 16
```

## Output layout

```
out/
  tiles/{side,lid,aux}/0000.png      64x64 map tiles, numbered per section.
                                     .CMP block bytes are 1-based, so wall
                                     byte N is side/000(N-1).png
  sprites/<class>/IIII_CCC.png       IIII = absolute sprite index,
                                     CCC = index within its class
  deltas/IIII_<class>/NN_name.png    base sprite with delta NN applied,
                                     e.g. 07_left_front_door.png
  delta_sprites/IIII_<class>/
    NN_name_add.png                  with --delta-files: pixels delta NN paints
    NN_name_erase.png                pixels delta NN removes, key-coloured
                                     (absent when the delta erases nothing)
  palettes.png                       1024 CLUTs, one per row
  metadata.json                      header, section offsets, sprite index,
                                     38 vehicles, 102 objects, class bases,
                                     and with --delta-files a delta_sprites
                                     block giving the add/erase file names,
                                     pixel counts and bounds per delta

anim/
  groups.json                        every group: clut, frame count, detected
                                     cycles, uniform_cycles flag, name: null,
                                     and a `sheet` block giving columns, rows,
                                     cell_width/height, scale, background and
                                     per-frame index, class_index,
                                     sprite_index and x/y/width/height.
                                     All sheet coordinates are in final image
                                     pixels (already multiplied by scale), so
                                     frames can be sliced straight out of
                                     sheet.png. `scope` is "group" or "class";
                                     with a class sheet, `file` is relative to
                                     the output dir and first_cell/first_row/
                                     first_col locate the group inside it
  <class>/<class>_gNN_clutCCC/
    frame_00.png ...                 frames in order
    cycleN_Mframes.apng              looping preview, one per detected cycle
    sheet.png                        contact sheet, labelled
                                     <frame in group>/<frame in class>,
                                     cycle starts marked green
  <class>/sheet.png                  with --sheet-group-class, one merged
                                     sheet per class replacing the per-group
                                     ones
```

## Animations

The archive contains no names and no animation table — no strings, the spare byte in every sprite record is always zero, and the anim section is 41 bytes. GTA1 keeps its animation tables in the executable. So `groups` recovers grouping and ordering, and leaves naming to you.

Grouping is two stages. Sprites are first partitioned by contiguous runs of their `clut` (virtual palette) field, which never interleave. Each run is then cut into cycles where the set of frame dimensions over the next four frames is disjoint from the previous four, with each cut slid ±3 frames to wherever the mean frame size either side is most separated. The second stage matters: detection alone splits three of the six 16-frame ped groups as 7+9 rather than 8+8.

Groups whose cycles all came out the same length (≥ 4) get `uniform_cycles: true` in `groups.json`. Those are the confident ones — in STYLE001 that is six ped groups of 8+8 and two object groups. Short groups of 2–5 frames are not cycles and are left whole. Odd-sized groups (29, 35, 19 frames) are proposals worth checking on the contact sheet.

`groups.json` gives every group a `name: null` to fill in. Watching ~25 APNGs beats inspecting 295 stills. Keep the names in a separate file so they survive a re-extract. For the real animation names, OpenGTA's spriteplayer and Carnage3D's ped animation tables are the community references.

## Notes

- Colour index 0 is transparent everywhere; `--background` overrides this.
- APNG previews are plain PNG plus `acTL`/`fcTL`/`fdAT` chunks, so they open in any browser and in Pillow.
- The 8-bit `.GRY` and `.GRX` variants are detected and their pixel layout should decode, but their palette scheme differs and colours will be wrong; the tool warns and continues. Only `.G24` is verified.

## File format

The full binary layout — header, sections, tiles, palettes, sprites, vehicles, objects and deltas — is documented in **[FORMAT.md](FORMAT.md)**. It's verified against `STYLE001.G24` and notes which parts are confirmed versus inferred, so it doubles as a reference for anyone implementing their own reader.

## License

`g24tool` is free software, licensed under the **GNU General Public License v3.0** (see [LICENSE](LICENSE)). You're free to use, study, share and modify it; the full text and terms are in the `LICENSE` file.

Copyright (C) 2026 Sander van Melsen

