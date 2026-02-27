# mgz-fast

A stripped-down version of [aoc-mgz](https://github.com/happyleavesaoc/aoc-mgz), tailored specifically for the needs of [AoE2Insights](https://aoe2insights.com). It contains only the bare essentials required for fast header and body parsing -- everything else has been removed.

## Supported Formats

- Userpatch 1.5 (`.mgz`)
- HD Edition 5.8 (`.aoe2record`)
- Definitive Edition (`.aoe2record`)

## Installation

```bash
pip install mgz-fast
```

## Usage

### Parsing the Header

Extract game metadata like players, map, version, and settings from a recorded game file.

```python
from mgz.fast.header import parse

with open("match.aoe2record", "rb") as f:
    header = parse(f)

print(header["version"])          # Version.DE
print(header["game_version"])     # e.g. "7.7"
print(header["save_version"])     # e.g. 13.34
```

### Players

```python
from mgz.fast.header import parse

with open("match.aoe2record", "rb") as f:
    header = parse(f)

for player in header["players"]:
    print(player["name"])
    print(player["civilization_id"])
    print(player["color_id"])
    print(player["position"])     # {"x": ..., "y": ...}
```

For Definitive Edition recordings, additional player data is available:

```python
for de_player in header["de"]["players"]:
    print(de_player["name"])
    print(de_player["censored_name"])
    print(de_player["team_id"])
```

### Map and Game Settings

```python
from mgz.fast.header import parse

with open("match.aoe2record", "rb") as f:
    header = parse(f)

print(header["scenario"]["map_id"])
print(header["lobby"]["seed"])
print(header["lobby"]["population"])
print(header["lobby"]["game_type_id"])
print(header["metadata"]["speed"])
```

### Parsing the Body (Actions)

Iterate over in-game operations like player actions, chat messages, and sync ticks.

```python
import os
from mgz.fast import operation, meta
from mgz.fast.header import parse
from mgz.fast.enums import Operation, Action

with open("match.aoe2record", "rb") as f:
    eof = os.fstat(f.fileno()).st_size
    header = parse(f)
    meta(f)

    while f.tell() < eof:
        try:
            op_type, payload = operation(f)
        except EOFError:
            break

        if op_type == Operation.ACTION:
            action_type, action_data = payload
            if action_type == Action.RESIGN:
                print(f"Player {action_data['player_id']} resigned")
            elif action_type == Action.RESEARCH:
                print(f"Player {action_data['player_id']} researched {action_data['technology_id']}")
            elif action_type == Action.BUILD:
                print(f"Player {action_data['player_id']} built {action_data['building_id']}")

        elif op_type == Operation.CHAT:
            print(f"Chat: {payload}")
```

### Extracting Chat Messages

```python
import os
from mgz.fast import operation, meta
from mgz.fast.header import parse
from mgz.fast.enums import Operation

with open("match.aoe2record", "rb") as f:
    eof = os.fstat(f.fileno()).st_size
    header = parse(f)
    meta(f)

    while f.tell() < eof:
        try:
            op_type, payload = operation(f)
        except EOFError:
            break

        if op_type == Operation.CHAT:
            print(payload.decode("utf-8", errors="replace"))
```

### Calculating Game Duration

```python
import os
from mgz.fast import operation, meta
from mgz.fast.header import parse
from mgz.fast.enums import Operation

with open("match.aoe2record", "rb") as f:
    eof = os.fstat(f.fileno()).st_size
    header = parse(f)
    meta(f)

    elapsed_ms = 0
    while f.tell() < eof:
        try:
            op_type, payload = operation(f)
        except EOFError:
            break

        if op_type == Operation.SYNC:
            increment, checksum, data = payload
            elapsed_ms += increment

    minutes = elapsed_ms / 1000 / 60
    print(f"Game duration: {minutes:.1f} minutes")
```

## CLI Tools

Installing `mgz-fast` also provides four command-line utilities for working with recorded game files.

### mgz-parse-header

Parse the header of a recorded game and output it as JSON.

```bash
# Parse header and write JSON to file
mgz-parse-header match.aoe2record -o header.json

# Parse with debug logging (useful for troubleshooting)
mgz-parse-header match.aoe2record --debug

# Also works with ZIP archives
mgz-parse-header match.zip -o header.json
```

### mgz-parse-body

Parse the body (actions/events) of a recorded game. Expects an extracted body file as input (see `mgz-extract`) and outputs JSON Lines to stdout.

```bash
# Parse body and print operations as JSON Lines
mgz-parse-body match.body.bin

# Pretty-print each operation
mgz-parse-body match.body.bin --indent 2
```

### mgz-extract

Extract the header and body from a recorded game into separate binary files. The header is automatically decompressed.

```bash
# Extract to default paths (<name>.header.bin, <name>.body.bin)
mgz-extract match.aoe2record

# Specify custom output paths
mgz-extract match.aoe2record --header h.bin --body b.bin
```

### mgz-dump

Hex-dump arbitrary byte ranges from a recorded game's header or body. Useful for reverse engineering and debugging.

```bash
# Dump first 256 bytes of the decompressed header
mgz-dump match.aoe2record header

# Dump 128 bytes starting at offset 0x2e0
mgz-dump match.aoe2record header --offset 0x2e0 --length 128

# Dump the beginning of the body
mgz-dump match.aoe2record body --offset 0 --length 64
```

## Header Fields Reference

The dictionary returned by `parse()` contains:

| Key | Type | Description |
|---|---|---|
| `version` | `Version` | Game version (`USERPATCH15`, `DE`, `HD`) |
| `game_version` | `str` | Version string |
| `save_version` | `float` | Save file version |
| `players` | `list[dict]` | Player info (name, civ, color, position, diplomacy) |
| `map` | `dict` | Map dimensions and tile data |
| `scenario` | `dict` | Map ID, difficulty, scenario filename |
| `lobby` | `dict` | Seed, population, game type, chat, lock teams |
| `metadata` | `dict` | Game speed, perspective owner, cheats |
| `de` | `dict\|None` | DE-specific data (extended player info, settings) |
| `hd` | `dict\|None` | HD-specific data |

## License

MIT
