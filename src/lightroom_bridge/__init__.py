"""lightroom_bridge -- drive Adobe Lightroom Classic from a CLI or an MCP server.

Two entry points share one transport core (:mod:`lightroom_bridge._core`):

* ``lrc``              -- the command-line client (:mod:`lightroom_bridge.cli`)
* ``lightroom-bridge`` -- the MCP server (:mod:`lightroom_bridge.mcp_server`)

The core is pure stdlib (Python 3.9+). The MCP server additionally needs
``fastmcp`` and a newer interpreter, so it is imported lazily, never here.
"""

__version__ = "0.3.0"

# Re-export the stable seam for embedders. Importing _core is stdlib-only.
from ._core import call, ensure_daemon, daemon_control  # noqa: E402,F401

__all__ = ["__version__", "call", "ensure_daemon", "daemon_control"]
