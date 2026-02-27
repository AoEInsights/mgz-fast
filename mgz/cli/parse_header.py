#!/usr/bin/env python3
"""Parse the header of an MGZ replay using the fast parser.

Input: a raw .mgz file or a .zip archive containing one.
Output: parsed header data as JSON, written to -o file (or stdout if given).
        Without -o the parse is still attempted; useful with --debug.
"""

import argparse
import io
import json
import logging
import sys
import zipfile
from enum import Enum
from pathlib import Path

from mgz.fast import header as fast_header


class _Encoder(json.JSONEncoder):
    def default(self, obj):
        if isinstance(obj, Enum):
            return obj.name
        if isinstance(obj, bytes):
            return obj.hex()
        if hasattr(obj, 'hexdigest') and callable(obj.hexdigest):
            return obj.hexdigest()
        return super().default(obj)


def load_mgz_bytes(path):
    path = Path(path)
    if not path.exists():
        print(f"Error: file not found: {path}", file=sys.stderr)
        sys.exit(1)
    if zipfile.is_zipfile(path):
        with zipfile.ZipFile(path) as zf:
            names = zf.namelist()
            mgz_names = [n for n in names if n.lower().endswith('.mgz')]
            if not mgz_names:
                mgz_names = names[:1]
            if not mgz_names:
                print("Error: ZIP archive is empty", file=sys.stderr)
                sys.exit(1)
            if len(mgz_names) > 1:
                print(f"Warning: multiple candidates in ZIP, using '{mgz_names[0]}'", file=sys.stderr)
            return zf.read(mgz_names[0])
    return path.read_bytes()


def main():
    parser = argparse.ArgumentParser(
        description='Parse the header of an MGZ replay file (or ZIP) using the fast parser.'
    )
    parser.add_argument('rec_path', help='Path to the .mgz file or .zip archive')
    parser.add_argument('-o', '--output', metavar='PATH',
                        help='Write parsed JSON to this file (default: no JSON output)')
    parser.add_argument('--indent', type=int, default=2,
                        help='JSON indentation (default: 2, use 0 for compact)')
    parser.add_argument('--debug', action='store_true',
                        help='Enable debug logging from the fast header parser')
    args = parser.parse_args()

    if args.debug:
        logging.basicConfig(
            level=logging.DEBUG,
            format='%(name)s %(levelname)s %(message)s',
            stream=sys.stderr,
        )
    else:
        logging.basicConfig(level=logging.WARNING)

    raw = load_mgz_bytes(args.rec_path)
    data = io.BytesIO(raw)

    try:
        result = fast_header.parse(data)
    except RuntimeError as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)

    if args.output:
        indent = args.indent if args.indent > 0 else None
        out = Path(args.output)
        out.write_text(json.dumps(result, cls=_Encoder, indent=indent))
        print(f"Written to {out}", file=sys.stderr)


if __name__ == '__main__':
    main()
