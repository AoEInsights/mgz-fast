#!/usr/bin/env python3
"""Parse the body of an MGZ replay using the fast parser.

Input: an extracted .body.bin file (as produced by extract_mgz.py).
Output: one JSON object per operation, written to stdout (JSON Lines format).
"""

import argparse
import io
import json
import sys
from enum import Enum
from pathlib import Path

from mgz import fast


class _Encoder(json.JSONEncoder):
    def default(self, obj):
        if isinstance(obj, Enum):
            return obj.name
        if isinstance(obj, bytes):
            return obj.hex()
        return super().default(obj)


def main():
    parser = argparse.ArgumentParser(
        description='Parse the body of an MGZ replay using the fast parser (JSON Lines output).'
    )
    parser.add_argument('body_path', help='Path to the body file (e.g. game.body.bin)')
    parser.add_argument('--indent', type=int, default=None, help='JSON indentation per line (default: compact)')
    args = parser.parse_args()

    path = Path(args.body_path)
    if not path.exists():
        print(f"Error: file not found: {path}", file=sys.stderr)
        sys.exit(1)

    data = io.BytesIO(path.read_bytes())
    indent = args.indent if args.indent and args.indent > 0 else None

    try:
        fast.meta(data)
    except ValueError as e:
        print(f"Error reading body meta: {e}", file=sys.stderr)
        sys.exit(1)

    while True:
        try:
            op_type, payload = fast.operation(data)
        except EOFError:
            break
        record = {'op': op_type.name if isinstance(op_type, Enum) else str(op_type)}
        if payload is not None:
            record['payload'] = payload
        print(json.dumps(record, cls=_Encoder, indent=indent))


if __name__ == '__main__':
    main()
