#!/usr/bin/env python3
"""Hex-dump arbitrary byte ranges from an MGZ replay's header or body.

The header is automatically decompressed before dumping.
Input can be a raw .mgz file or a .zip archive containing one.

Examples:
    ./dump_mgz.py rec.mgz header --offset 600 --length 256
    ./dump_mgz.py rec.zip  body   --offset 0   --length 128
    ./dump_mgz.py rec.mgz header --offset 0x2e0            # hex offset, default length
"""

import argparse
import io
import struct
import sys
import zipfile
import zlib
from pathlib import Path

ZLIB_WBITS = -15
DEFAULT_LENGTH = 256


def load_mgz_bytes(path):
    path = Path(path)
    if not path.exists():
        print(f"Error: file not found: {path}", file=sys.stderr)
        sys.exit(1)
    if zipfile.is_zipfile(path):
        with zipfile.ZipFile(path) as zf:
            names = zf.namelist()
            mgz_names = [n for n in names if n.lower().endswith('.mgz') or n.lower().endswith('.aoe2record')]
            if not mgz_names:
                mgz_names = names[:1]
            if not mgz_names:
                print("Error: ZIP archive is empty", file=sys.stderr)
                sys.exit(1)
            if len(mgz_names) > 1:
                print(f"Warning: multiple candidates in ZIP, using '{mgz_names[0]}'", file=sys.stderr)
            return zf.read(mgz_names[0])
    return path.read_bytes()


def get_header(raw):
    """Return decompressed header bytes."""
    if len(raw) < 8:
        print("Error: file too small", file=sys.stderr)
        sys.exit(1)
    header_length, chapter_address = struct.unpack('<II', raw[:8])
    compressed = raw[8:header_length]
    try:
        return zlib.decompress(compressed, wbits=ZLIB_WBITS)
    except zlib.error as e:
        print(f"Error: failed to decompress header: {e}", file=sys.stderr)
        sys.exit(1)


def get_body(raw):
    """Return body bytes."""
    header_length, = struct.unpack('<I', raw[:4])
    return raw[header_length:]


def hexdump(data, base_offset=0):
    for i in range(0, len(data), 16):
        chunk = data[i:i+16]
        offset = base_offset + i
        hex_part = ' '.join(f'{b:02x}' for b in chunk)
        asc_part = ''.join(chr(b) if 32 <= b < 127 else '.' for b in chunk)
        print(f"  {offset:08x}  {hex_part:<47}  {asc_part}")


def auto_int(x):
    """Accept decimal or hex (0x...) integers."""
    return int(x, 0)


def main():
    parser = argparse.ArgumentParser(
        description='Hex-dump byte ranges from an MGZ replay header (decompressed) or body.'
    )
    parser.add_argument('rec_path', help='Path to .mgz file or .zip archive')
    parser.add_argument('section', choices=['header', 'body'],
                        help='Which section to dump')
    parser.add_argument('--offset', '-s', type=auto_int, default=0,
                        help='Start offset in bytes (decimal or 0x hex, default: 0)')
    parser.add_argument('--length', '-n', type=auto_int, default=DEFAULT_LENGTH,
                        help=f'Number of bytes to dump (default: {DEFAULT_LENGTH})')
    args = parser.parse_args()

    raw = load_mgz_bytes(args.rec_path)

    if args.section == 'header':
        data = get_header(raw)
        label = 'header (decompressed)'
    else:
        data = get_body(raw)
        label = 'body'

    total = len(data)
    offset = args.offset
    length = args.length

    if offset >= total:
        print(f"Error: offset {offset} (0x{offset:x}) >= section size {total} (0x{total:x})", file=sys.stderr)
        sys.exit(1)

    if offset + length > total:
        length = total - offset
        print(f"Note: clamped to {length} bytes (section ends at 0x{total:x})", file=sys.stderr)

    print(f"[{label}] offset=0x{offset:x} ({offset}) length=0x{length:x} ({length}) total=0x{total:x} ({total})")
    hexdump(data[offset:offset + length], base_offset=offset)


if __name__ == '__main__':
    main()
