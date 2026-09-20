#!/usr/bin/env python3
"""
Compatibility shim. The bridge now lives in the importable ``lightroom_bridge``
package under ``src/``; this keeps ``./lrc …`` (and ``python3 bridge/lrc.py …``)
working from a source checkout without installing anything.

If you've installed the package (``pip install .`` or ``uvx lightroom-bridge``),
prefer the ``lrc`` console script instead.
"""

import os
import sys

# Put the repo's src/ on the path so `lightroom_bridge` imports without install.
_SRC = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src")
if os.path.isdir(os.path.join(_SRC, "lightroom_bridge")) and _SRC not in sys.path:
    sys.path.insert(0, _SRC)

from lightroom_bridge.cli import main  # noqa: E402

if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
