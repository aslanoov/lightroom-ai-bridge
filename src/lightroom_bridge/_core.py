#!/usr/bin/env python3
"""
lightroom_bridge._core -- transport to the "Claude Bridge" Lightroom plug-in.

This module is the shared, dependency-free core used by BOTH the `lrc` CLI
(`lightroom_bridge.cli`) and the MCP server (`lightroom_bridge.mcp_server`).
It is pure stdlib and stays compatible with Python 3.9 so the CLI keeps running
on stock macOS Python; only the MCP server requires a newer interpreter.

Architecture
------------
The Lightroom plug-in (ClaudeBridge.lrplugin) opens two localhost TCP sockets:

    49463  command port   (we WRITE Lua-literal command lines here)
    49464  response port  (we READ JSON response lines from here)

A tiny background *daemon* holds both connections open (the plug-in is a
single-client server, so exactly one durable connection must be shared by every
caller) and exposes a Unix domain socket:

    ~/.claude-lrc-bridge/cli.sock

Each caller (CLI invocation or MCP tool call) is a short-lived UDS client: it
makes sure the daemon is running (auto-starting it if needed), sends one command
line, reads one JSON response, and returns it.

Wire formats
------------
  caller -> daemon : one JSON object per line  {"id":N,"cmd":..,"params":..}
  daemon -> Lr     : one Lua table literal per line  {id=N,cmd="..",params={..}}
  Lr     -> daemon : one JSON object per line  {"id":N,"ok":true,"result":..}
  daemon -> caller : that same JSON object
"""

from __future__ import annotations

import fcntl
import json
import os
import socket
import subprocess
import sys
import threading
import time

# --------------------------------------------------------------------------- #
# Configuration
# --------------------------------------------------------------------------- #

DEFAULT_RECV_PORT = 49463   # Lr listens; we send commands
DEFAULT_SEND_PORT = 49464   # Lr listens; we read responses

BRIDGE_DIR = os.path.expanduser("~/.claude-lrc-bridge")
HANDSHAKE = os.path.join(BRIDGE_DIR, "bridge.json")
UDS_PATH = os.path.join(BRIDGE_DIR, "cli.sock")
DAEMON_LOG = os.path.join(BRIDGE_DIR, "daemon.log")
DAEMON_LOCK = os.path.join(BRIDGE_DIR, "daemon.lock")
PREVIEW_DIR = os.path.join(BRIDGE_DIR, "previews")

LR_HOST = "127.0.0.1"
LR_READ_TIMEOUT = 90.0      # render of a big raw can take a while
CLI_TIMEOUT = 120.0


def ports():
    """Return (recv_port, send_port), preferring the plug-in's handshake file."""
    recv, send = DEFAULT_RECV_PORT, DEFAULT_SEND_PORT
    try:
        with open(HANDSHAKE, "r") as f:
            info = json.load(f)
        recv = int(info.get("recvPort", recv))
        send = int(info.get("sendPort", send))
    except Exception:
        pass
    return recv, send


# --------------------------------------------------------------------------- #
# Lua-literal serialization (Python value -> Lua table literal string)
# --------------------------------------------------------------------------- #

_LUA_KEYWORDS = {
    "and", "break", "do", "else", "elseif", "end", "false", "for", "function",
    "goto", "if", "in", "local", "nil", "not", "or", "repeat", "return",
    "then", "true", "until", "while",
}


def _is_ident(s):
    if not s or s in _LUA_KEYWORDS:
        return False
    if not (s[0].isalpha() or s[0] == "_"):
        return False
    return all(c.isalnum() or c == "_" for c in s)


def _lua_string(s):
    out = ['"']
    for ch in s:
        o = ord(ch)
        if ch == '"':
            out.append('\\"')
        elif ch == "\\":
            out.append("\\\\")
        elif ch == "\n":
            out.append("\\n")
        elif ch == "\r":
            out.append("\\r")
        elif ch == "\t":
            out.append("\\t")
        elif o < 32 or o == 127:
            out.append("\\%d" % o)
        else:
            out.append(ch)  # printable ASCII or UTF-8 char, passed through
    out.append('"')
    return "".join(out)


def to_lua(value):
    """Serialize a JSON-ish Python value to a Lua literal the plug-in can parse."""
    if value is None:
        return "nil"
    if value is True:
        return "true"
    if value is False:
        return "false"
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float):
        if value != value or value in (float("inf"), float("-inf")):
            raise ValueError("non-finite numbers are not allowed")
        return repr(value)
    if isinstance(value, str):
        return _lua_string(value)
    if isinstance(value, dict):
        parts = []
        for k, v in value.items():
            key = str(k)
            lk = key if _is_ident(key) else "[" + _lua_string(key) + "]"
            parts.append(lk + "=" + to_lua(v))
        return "{" + ",".join(parts) + "}"
    if isinstance(value, (list, tuple)):
        return "{" + ",".join(to_lua(v) for v in value) + "}"
    raise TypeError("cannot serialize %r" % type(value))


# --------------------------------------------------------------------------- #
# Line-buffered socket reader
# --------------------------------------------------------------------------- #

class LineSocket:
    def __init__(self, sock):
        self.sock = sock
        self.buf = b""

    def readline(self):
        while b"\n" not in self.buf:
            chunk = self.sock.recv(65536)
            if not chunk:
                raise ConnectionError("socket closed by peer")
            self.buf += chunk
        line, self.buf = self.buf.split(b"\n", 1)
        return line.rstrip(b"\r").decode("utf-8", "replace")

    def sendline(self, text):
        self.sock.sendall((text + "\n").encode("utf-8"))

    def close(self):
        try:
            self.sock.close()
        except Exception:
            pass


# --------------------------------------------------------------------------- #
# Daemon: holds the Lightroom connection, serves the UDS
# --------------------------------------------------------------------------- #

def _dlog(msg):
    try:
        with open(DAEMON_LOG, "a") as f:
            f.write("%.3f %s\n" % (time.time(), msg))
    except Exception:
        pass


def _lr_timeout(cmd):
    """Socket read timeout for one command. Renders take long, and the plug-in
    honors a params.timeout watchdog -- grow the transport timeout with it so a
    caller-requested long render isn't cut off at the socket layer. Other
    commands get 45s: above the plug-in's 30s catalog write-access window, so
    a busy catalog surfaces as the plug-in's own error, not a transport cut."""
    if cmd.get("cmd") not in ("render", "thumb"):
        return 45
    try:
        t = float((cmd.get("params") or {}).get("timeout") or 0)
    except (TypeError, ValueError):
        t = 0
    return max(120, t + 10)


# Commands safe to send AGAIN after a transport error: pure reads plus renders
# (which only write a preview file). Anything else may already have applied
# inside Lightroom before the link dropped -- resending could double-execute a
# catalog write, so those return an error instead of retrying.
_RESEND_SAFE = frozenset((
    "ping", "status", "list_selected", "get_settings", "get_metadata",
    "get_value", "get_range", "preset_list", "mask_list", "snapshot_list",
    "help", "render", "thumb",
))


class LrLink:
    """Persistent connection to Lightroom's two sockets."""

    def __init__(self):
        self.reader = None   # LineSocket on SEND_PORT (Lr -> us)
        self.writer = None   # LineSocket on RECV_PORT (us -> Lr)

    def connected(self):
        return self.reader is not None and self.writer is not None

    def connect(self, timeout=8.0):
        recv_port, send_port = ports()
        deadline = time.time() + timeout
        last = None
        while time.time() < deadline:
            try:
                # Connect the response (read) socket first so no reply is missed.
                r = socket.create_connection((LR_HOST, send_port), timeout=5)
                w = socket.create_connection((LR_HOST, recv_port), timeout=5)
                r.settimeout(LR_READ_TIMEOUT)
                w.settimeout(10)
                self.reader = LineSocket(r)
                self.writer = LineSocket(w)
                _dlog("connected to Lightroom (recv %d / send %d)" % (recv_port, send_port))
                return True
            except OSError as e:
                last = e
                self.close()
                time.sleep(0.3)
        _dlog("could not connect to Lightroom: %s" % last)
        return False

    def close(self):
        if self.reader:
            self.reader.close()
        if self.writer:
            self.writer.close()
        self.reader = None
        self.writer = None

    def request(self, cmd):
        """Send one command dict to Lr and return the matching response dict."""
        if not self.connected():
            if not self.connect():
                return {"ok": False, "error": "Lightroom not reachable. Is Lightroom "
                        "running with 'Claude Bridge' started?"}
        want = cmd.get("id")
        # Quick commands should fail fast; renders/exports may legitimately take long.
        timeout = _lr_timeout(cmd)
        try:
            self.reader.sock.settimeout(timeout)
            self.writer.sendline(to_lua(cmd))
            return self._read_response(want)
        except (OSError, ConnectionError) as e:
            _dlog("link error (%s); reconnecting" % e)
            self.close()
            if cmd.get("cmd") not in _RESEND_SAFE:
                # The command may have reached Lightroom before the link died;
                # resending could apply a catalog write twice.
                self.connect()
                return {"ok": False, "error": "connection to Lightroom lost while "
                        "running %r (%s); the command may or may not have applied "
                        "-- verify state before retrying" % (cmd.get("cmd"), e)}
            if not self.connect():
                return {"ok": False, "error": "Lost connection to Lightroom and could "
                        "not reconnect (%s)." % e}
            try:
                self.reader.sock.settimeout(timeout)
                self.writer.sendline(to_lua(cmd))
                return self._read_response(want)
            except (OSError, ConnectionError) as e2:
                self.close()
                return {"ok": False, "error": "Lightroom request failed: %s" % e2}

    def _read_response(self, want_id):
        # Read lines until we see the JSON response carrying our id.
        for _ in range(1000):
            line = self.reader.readline()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except ValueError:
                _dlog("non-JSON from Lr: %r" % line)
                continue
            if want_id is None or obj.get("id") == want_id or "id" not in obj:
                return obj
            # otherwise a stale/foreign line; keep reading
        return {"ok": False, "error": "no matching response from Lightroom"}


def _client_gone(conn):
    """True if the UDS client already disconnected (e.g. it timed out while
    this request sat queued behind a long render). Executing its command then
    would apply a write the caller was told FAILED -- drop it instead."""
    try:
        conn.setblocking(False)
        try:
            return conn.recv(1, socket.MSG_PEEK) == b""
        except (BlockingIOError, InterruptedError):
            return False  # no data, still connected and waiting
        finally:
            conn.settimeout(CLI_TIMEOUT)
    except OSError:
        return False


def run_daemon():
    os.makedirs(BRIDGE_DIR, exist_ok=True)
    # Single instance: if a live daemon already answers, exit quietly.
    if _uds_alive():
        _dlog("daemon already running; exiting")
        return 0
    try:
        os.unlink(UDS_PATH)
    except FileNotFoundError:
        pass

    srv = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    srv.bind(UDS_PATH)
    srv.listen(16)
    _dlog("daemon listening on %s" % UDS_PATH)

    link = LrLink()  # connects lazily on the first request

    try:
        while True:
            conn, _ = srv.accept()
            conn.settimeout(CLI_TIMEOUT)
            line_io = LineSocket(conn)
            try:
                line = line_io.readline()
                req = json.loads(line)
                if req.get("_ctl") == "shutdown":
                    line_io.sendline(json.dumps({"ok": True, "result": "daemon stopping"}))
                    line_io.close()
                    break
                if req.get("_ctl") == "ping":
                    resp = {"ok": True, "result": {"daemon": True, "lrConnected": link.connected()}}
                else:
                    # This request may have queued behind a long command. If the
                    # caller has already given up (deadline passed or socket
                    # closed), do NOT execute it -- a "failed" write must not
                    # silently apply minutes later (nor double-apply on retry).
                    deadline = req.pop("_deadline", None)
                    if deadline is not None and time.time() > float(deadline):
                        _dlog("dropping expired request: %s" % req.get("cmd"))
                        resp = {"ok": False, "error": "daemon: request expired "
                                "before it could be dispatched (queue delay)"}
                    elif _client_gone(conn):
                        _dlog("dropping request from departed client: %s" % req.get("cmd"))
                        resp = None
                    else:
                        resp = link.request(req)
                if resp is not None:
                    line_io.sendline(json.dumps(resp))
            except Exception as e:
                try:
                    line_io.sendline(json.dumps({"ok": False, "error": "daemon error: %s" % e}))
                except Exception:
                    pass
            finally:
                line_io.close()
    finally:
        link.close()
        srv.close()
        try:
            os.unlink(UDS_PATH)
        except FileNotFoundError:
            pass
        _dlog("daemon stopped")
    return 0


# --------------------------------------------------------------------------- #
# Client side
# --------------------------------------------------------------------------- #

def _uds_alive():
    if not os.path.exists(UDS_PATH):
        return False
    try:
        c = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        c.settimeout(2)
        c.connect(UDS_PATH)
        c.close()
        return True
    except OSError:
        return False


def ensure_daemon():
    if _uds_alive():
        return True
    os.makedirs(BRIDGE_DIR, exist_ok=True)
    # Serialize spawning: concurrent callers (e.g. overlapping MCP tool calls)
    # could otherwise both see the socket dead and start two daemons, and the
    # loser's unlink-and-rebind orphans the winner -- which may already hold
    # the single-client Lightroom connection.
    with open(DAEMON_LOCK, "w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        try:
            if _uds_alive():  # another caller won the race while we waited
                return True
            # Spawn the daemon as `python -m lightroom_bridge --run-daemon`.
            # Add this package's parent dir to PYTHONPATH so the spawned
            # interpreter can import it when running from a source checkout
            # (harmless once pip/uvx-installed).
            env = dict(os.environ)
            pkg_parent = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
            env["PYTHONPATH"] = pkg_parent + os.pathsep + env.get("PYTHONPATH", "")
            subprocess.Popen(
                [sys.executable, "-m", "lightroom_bridge", "--run-daemon"],
                stdout=open(DAEMON_LOG, "a"),
                stderr=subprocess.STDOUT,
                stdin=subprocess.DEVNULL,
                start_new_session=True,
                env=env,
            )
            for _ in range(40):  # up to ~8s
                if _uds_alive():
                    return True
                time.sleep(0.2)
            return False
        finally:
            fcntl.flock(lock, fcntl.LOCK_UN)


def send_via_daemon(cmd):
    if not ensure_daemon():
        return {"ok": False, "error": "could not start the lrc daemon; see %s" % DAEMON_LOG}
    c = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    # The daemon replies only after Lightroom does, so the client read must
    # outlast the Lightroom-side timeout for the command (plus queueing slack).
    client_timeout = max(CLI_TIMEOUT, _lr_timeout(cmd) + 30)
    c.settimeout(client_timeout)
    c.connect(UDS_PATH)
    io = LineSocket(c)
    # Tell the daemon when we'll stop listening, so a request that queued
    # behind a long command isn't executed after we've already given up.
    cmd = dict(cmd)
    cmd["_deadline"] = time.time() + client_timeout - 2
    io.sendline(json.dumps(cmd))
    resp = io.readline()
    io.close()
    return json.loads(resp)


_id_lock = threading.Lock()
_next_id = [int(time.time() * 1000) % 100000]


def call(cmd_name, params=None):
    """Send one plug-in command through the daemon and return its response dict.

    Returns ``{"ok": True, "result": ...}`` or ``{"ok": False, "error": ...}``.
    Transport failures are folded into the same shape instead of raising.
    This is the single seam both the CLI and the MCP server build on.
    """
    with _id_lock:  # FastMCP may run tool calls on concurrent threads
        _next_id[0] += 1
        req_id = _next_id[0]
    cmd = {"id": req_id, "cmd": cmd_name, "params": params or {}}
    try:
        return send_via_daemon(cmd)
    except (OSError, ConnectionError, ValueError) as e:
        return {"ok": False, "error": "transport error: %s" % e}


def _send_ctl(ctl):
    """Open the UDS, send one {_ctl: ...} control message, return the response."""
    c = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    c.settimeout(5)
    c.connect(UDS_PATH)
    io = LineSocket(c)
    io.sendline(json.dumps({"_ctl": ctl}))
    resp = json.loads(io.readline())
    io.close()
    return resp


def daemon_control(action):
    """Manage the background daemon. `action`: "status" | "stop" | "start"."""
    if action == "start":
        ok = ensure_daemon()
        return {"ok": ok, "result": "daemon running" if ok else "failed to start"}
    if action == "stop":
        if not _uds_alive():
            return {"ok": True, "result": "daemon not running"}
        return _send_ctl("shutdown")
    # status (the daemon reports liveness via the "ping" control message)
    if not _uds_alive():
        return {"ok": True, "result": {"daemon": False}}
    return _send_ctl("ping")
