#!/usr/bin/env python3
"""Extract header and body from an MGZ replay file.

The input can be an .mgz file directly or a .zip archive containing one.
The header is stored zlib-compressed (no zlib framing, wbits=-15) in the MGZ
and will always be decompressed on output.
"""

import argparse
import io
import struct
import sys
import zipfile
import zlib
from pathlib import Path


def load_mgz_bytes(rec_path):
    """Return raw bytes of the MGZ file, unpacking a ZIP if necessary."""
    path = Path(rec_path)
    if not path.exists():
        print(f"Error: file not found: {path}", file=sys.stderr)
        sys.exit(1)

    if zipfile.is_zipfile(path):
        with zipfile.ZipFile(path) as zf:
            names = zf.namelist()
            mgz_names = [n for n in names if n.lower().endswith('.mgz')]
            if not mgz_names:
                # Fall back to first entry if none has .mgz extension
                mgz_names = names[:1]
            if not mgz_names:
                print("Error: ZIP archive is empty", file=sys.stderr)
                sys.exit(1)
            if len(mgz_names) > 1:
                print(
                    f"Warning: multiple candidates in ZIP, using '{mgz_names[0]}'",
                    file=sys.stderr,
                )
            return zf.read(mgz_names[0])

    return path.read_bytes()


def split_header(raw):
    """Return (header_length, chapter_address_bytes, compressed_data, body_data).

    header_length       – value of the first 4 bytes
    chapter_address_bytes – 4 bytes if present, else b''
    compressed_data     – the raw zlib stream
    body_data           – everything after the header section
    """
    if len(raw) < 4:
        print("Error: file too small to be a valid MGZ replay", file=sys.stderr)
        sys.exit(1)

    header_length, = struct.unpack('<I', raw[:4])
    if header_length > len(raw):
        print(
            f"Error: header_length ({header_length}) exceeds file size ({len(raw)})",
            file=sys.stderr,
        )
        sys.exit(1)

    # Peek at the next 4 bytes to decide whether chapter_address is present
    check, = struct.unpack('<I', raw[4:8])
    if check < 100_000_000:
        chapter_address_bytes = raw[4:8]
        compressed_data = raw[8:header_length]
    else:
        chapter_address_bytes = b''
        compressed_data = raw[4:header_length]

    body_data = raw[header_length:]
    return header_length, chapter_address_bytes, compressed_data, body_data


def decompress_header_data(compressed_data):
    """Decompress the zlib stream (no zlib framing, wbits=-15)."""
    try:
        return zlib.decompress(compressed_data, wbits=-15)
    except zlib.error as exc:
        print(f"Error: failed to decompress header: {exc}", file=sys.stderr)
        sys.exit(1)


def extract(rec_path, header_path=None, body_path=None):
    raw = load_mgz_bytes(rec_path)

    # Derive default output names from the input stem
    stem = Path(rec_path).stem  # strips .zip or .mgz
    base_dir = Path(rec_path).parent

    if header_path is None:
        header_path = base_dir / (stem + '.header.bin')
    else:
        header_path = Path(header_path)

    if body_path is None:
        body_path = base_dir / (stem + '.body.bin')
    else:
        body_path = Path(body_path)

    header_length, chapter_address_bytes, compressed_data, body_data = split_header(raw)

    decompressed = decompress_header_data(compressed_data)
    # Reassemble: keep the 4-byte length field and optional chapter_address,
    # then append the decompressed content.
    header_out = raw[:4] + chapter_address_bytes + decompressed

    header_path.write_bytes(header_out)
    body_path.write_bytes(body_data)

    print(f"Header ({len(header_out)} bytes) -> {header_path}")
    print(f"Body   ({len(body_data)} bytes)  -> {body_path}")


def main():
    parser = argparse.ArgumentParser(
        description='Extract header and body from an MGZ replay file (or a ZIP containing one).'
    )
    parser.add_argument(
        'rec_path',
        help='Path to the .mgz replay file or a .zip archive containing one',
    )
    parser.add_argument(
        '--header', metavar='PATH',
        help='Output path for the header (default: <name>.mgz-header)',
    )
    parser.add_argument(
        '--body', metavar='PATH',
        help='Output path for the body (default: <name>.mgz-body)',
    )
    args = parser.parse_args()
    extract(args.rec_path, args.header, args.body)


if __name__ == '__main__':
    main()
