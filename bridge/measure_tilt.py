#!/usr/bin/env python3
"""Compatibility entry point. Canonical line fitting lives in the package."""
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from lightroom_bridge._geometry_lines import *  # noqa: F401,F403
if __name__ == '__main__':
    main()
