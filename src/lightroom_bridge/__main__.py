"""`python -m lightroom_bridge` -- routes to the daemon or the CLI.

`--run-daemon` is how :func:`lightroom_bridge._core.ensure_daemon` spawns the
background daemon; everything else is the normal `lrc` CLI.
"""

import sys


def _main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    if "--run-daemon" in argv:
        from . import _core
        return _core.run_daemon()
    from .cli import main as cli_main
    return cli_main(argv)


if __name__ == "__main__":
    sys.exit(_main())
