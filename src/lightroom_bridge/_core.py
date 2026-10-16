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

    49463  command port   (we WRITE JSON command lines here)
    49464  response port  (we READ JSON response lines from here)

A tiny background *daemon* holds both connections open (the plug-in is a
single-client server, so exactly one durable connection must be shared by every
caller) and exposes a Unix domain socket:

    ~/.claude-lrc-bridge/cli.sock

Each caller (CLI invocation or MCP tool call) is a short-lived UDS client: it
makes sure the daemon is running (auto-starting it if needed), sends one command
line, reads one JSON response, and returns it.

Wire formats (protocol "json-1", plug-in 1.5+)
------------
  caller -> daemon : one JSON object per line  {"id":N,"cmd":..,"params":..}
  daemon -> Lr     : the same object plus "token" (the secret the plug-in
                     writes to ~/.claude-lrc-bridge/auth.token; the folder is
                     kept private, mode 0700)
  Lr     -> daemon : one JSON object per line  {"id":N,"ok":true,"result":..}
  daemon -> caller : that same JSON object

Liveness: before trusting the link (fresh connection, idle for a minute, or
after a timeout) the daemon pings with a 5s limit, so a busy or wedged plug-in
fails in seconds with a diagnosis (who owns the ports, handshake state)
instead of after a full command timeout.
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
AUTH_PATH = os.path.join(BRIDGE_DIR, "auth.token")
PROTOCOL = "json-1"         # must match Bridge.lua's PROTOCOL

LR_HOST = "127.0.0.1"
LR_READ_TIMEOUT = 90.0      # render of a big raw can take a while
CLI_TIMEOUT = 120.0
PROBE_TIMEOUT = 5.0         # liveness ping before trusting the link
IDLE_PROBE_S = 60.0         # re-probe after this much silence
# After a client leaves, the plug-in re-arms its command port and rebinds its
# send port (rate-limited to one rebind per second). A reconnect inside that
# window is refused or lands on the old, dead listener: LrLink.connect waits
# this long after any link it dropped. Measured live (2026-10-10): the window
# is ~0.25 s, up to ~1.13 s right after a previous rebind; 1.5 s leaves margin
# for a busy Lightroom loop.
RECONNECT_GRACE_S = 1.5
HANDOFF_GRACE_S = 15.0      # a fresh 'stopped' may be a reload in progress

LOG_MAX_BYTES = 5 * 1024 * 1024
# Renders land in PREVIEW_DIR and every caller copies them out immediately
# (--out, the MCP inline image), so they are disposable.
PREVIEW_MAX_AGE_S = 24 * 3600
PREVIEW_MAX_BYTES = 1 << 30
PREVIEW_MIN_AGE_S = 600     # never touch a render younger than this
MAINTENANCE_EVERY_S = 3600


def _ensure_private_dir():
    """The bridge folder holds the auth token: keep it owner-only (0700)."""
    os.makedirs(BRIDGE_DIR, mode=0o700, exist_ok=True)
    try:
        os.chmod(BRIDGE_DIR, 0o700)
    except OSError:
        pass


def _handshake():
    try:
        with open(HANDSHAKE, "r") as f:
            info = json.load(f)
        return info if isinstance(info, dict) else {}
    except Exception:
        return {}


def _handshake_age():
    """Seconds since the plug-in last wrote its handshake (inf if never)."""
    try:
        return time.time() - os.path.getmtime(HANDSHAKE)
    except OSError:
        return float("inf")


def _read_token():
    try:
        with open(AUTH_PATH, "r") as f:
            return f.read().strip() or None
    except OSError:
        return None


def ports():
    """Return (recv_port, send_port), preferring the plug-in's handshake file."""
    recv, send = DEFAULT_RECV_PORT, DEFAULT_SEND_PORT
    info = _handshake()
    try:
        recv = int(info.get("recvPort", recv))
        send = int(info.get("sendPort", send))
    except (TypeError, ValueError):
        pass
    return recv, send


# --------------------------------------------------------------------------- #
# Diagnosis: who owns the bridge ports, and what that means
# --------------------------------------------------------------------------- #

def port_owners(port_list):
    """{port: [{"pid": .., "command": ..}]} for LISTENING sockets, or None
    when lsof is unavailable."""
    lsof = "/usr/sbin/lsof"
    if not os.path.exists(lsof):
        return None
    owners = {}
    for port in port_list:
        try:
            r = subprocess.run(
                [lsof, "-nP", "+c", "0", "-iTCP:%d" % port, "-sTCP:LISTEN", "-Fpc"],
                stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                universal_newlines=True, timeout=2)  # x2 ports stays under the 5s status call
        except (OSError, subprocess.SubprocessError):
            return None
        found, pid = [], None
        for line in r.stdout.splitlines():
            if line.startswith("p"):
                pid = int(line[1:])
            elif line.startswith("c") and pid is not None:
                found.append({"pid": pid, "command": line[1:]})
        owners[port] = found
    return owners


def diagnose(unresponsive=False, owners=None):
    """Explain why Lightroom is unreachable. `unresponsive`: the plug-in
    accepted the connection but did not answer a ping in time."""
    recv, send = ports()
    hs = _handshake()
    if owners is None:
        owners = port_owners((recv, send))
    hs_info = {k: hs.get(k) for k in ("status", "plugin", "protocol", "lightroom", "updated")
               if k in hs}
    out = {"ports": {str(p): (owners or {}).get(p) for p in (recv, send)} if owners else None,
           "handshake": hs_info}
    if hs.get("status") == "running" and hs.get("protocol") != PROTOCOL:
        out["hint"] = ("The running Claude Bridge plug-in (%s) predates protocol %s. Reload it "
                       "(File > Plug-in Manager > Claude Bridge > Reload Plug-in) or restart "
                       "Lightroom." % (hs.get("plugin", "unknown version"), PROTOCOL))
        return out
    if owners is None:
        out["hint"] = ("Could not inspect the ports (lsof unavailable). Is Lightroom running "
                       "with the Claude Bridge plug-in started?")
        return out
    foreign = [(p, o) for p in (recv, send) for o in owners.get(p, [])
               if "lightroom" not in o["command"].lower()]
    lr_ports = [p for p in (recv, send)
                if any("lightroom" in o["command"].lower() for o in owners.get(p, []))]
    if foreign:
        p, o = foreign[0]
        out["hint"] = ("Port %d is held by %s (pid %d), not Lightroom. Quit that program, then "
                       "restart the plug-in (File > Plug-in Extras > Claude Bridge: Start / "
                       "Restart)." % (p, o["command"], o["pid"]))
    elif not lr_ports:
        out["hint"] = ("Nothing is listening on ports %d/%d: Lightroom Classic is closed, or the "
                       "Claude Bridge plug-in is stopped (File > Plug-in Extras > Claude Bridge: "
                       "Start / Restart)." % (recv, send))
    elif len(lr_ports) == 1:
        out["hint"] = ("Lightroom listens on only one bridge port; restart the plug-in (File > "
                       "Plug-in Extras > Claude Bridge: Stop, then Start / Restart).")
    elif unresponsive:
        out["hint"] = ("Lightroom holds both bridge ports but the plug-in did not answer a ping "
                       "within %gs: it is busy (a long render or export, or an open dialog in "
                       "Lightroom) or wedged. Check Lightroom for a dialog; if there is none, "
                       "restart the plug-in (File > Plug-in Extras > Claude Bridge: Stop, then "
                       "Start / Restart)." % PROBE_TIMEOUT)
    else:
        out["hint"] = "Lightroom is listening on both bridge ports."
    return out


# --------------------------------------------------------------------------- #
# Housekeeping: preview cache and log size
# --------------------------------------------------------------------------- #

def prune_previews(root=PREVIEW_DIR, now=None, max_age=PREVIEW_MAX_AGE_S,
                   max_bytes=PREVIEW_MAX_BYTES, min_age=PREVIEW_MIN_AGE_S):
    """Delete stale renders: anything older than `max_age`, then the oldest
    until the cache is under `max_bytes`. Only *.jpg/*.jpeg files at the top
    level are candidates, and nothing younger than `min_age` (a caller may
    still be copying it)."""
    now = time.time() if now is None else now
    entries = []
    try:
        names = os.listdir(root)
    except OSError:
        return {"removed": 0, "freedBytes": 0, "keptBytes": 0}
    for name in names:
        path = os.path.join(root, name)
        try:
            st = os.lstat(path)
            if os.path.islink(path) or not os.path.isfile(path) \
                    or not name.lower().endswith((".jpg", ".jpeg")):
                continue
            entries.append((st.st_mtime, st.st_size, path))
        except OSError:
            continue
    entries.sort()  # oldest first
    total = sum(e[1] for e in entries)
    removed = freed = 0
    for mtime, size, path in entries:
        age = now - mtime
        if age < min_age:
            break  # sorted: everything after this is younger still
        if age <= max_age and total <= max_bytes:
            continue
        try:
            os.remove(path)
        except OSError:
            continue
        total -= size
        freed += size
        removed += 1
    return {"removed": removed, "freedBytes": freed, "keptBytes": total}


def _rotate_log(path, max_bytes=LOG_MAX_BYTES):
    """Keep one previous generation (<path>.1) once a log passes max_bytes."""
    try:
        if os.path.getsize(path) > max_bytes:
            os.replace(path, path + ".1")
    except OSError:
        pass


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
        self.token = None
        self.suspect = True  # probe before the next command
        self.last_ok = 0.0
        self.dropped_at = 0.0  # when we last closed an open link
        self._probe_id = 0   # negative ids never collide with callers' ids

    def connected(self):
        return self.reader is not None and self.writer is not None

    def connect(self, timeout=8.0):
        recv_port, send_port = ports()
        deadline = time.time() + timeout
        last = None
        while time.time() < deadline:
            # After a client leaves, the plug-in needs a moment to re-arm its
            # command port and rebind its response port (rate-limited to one
            # rebind per second). A connection inside that window is refused
            # or lands on the dead listener, so never reconnect inside it.
            wait = RECONNECT_GRACE_S - (time.time() - self.dropped_at)
            if wait > 0:
                if time.time() + wait >= deadline:
                    break  # an attempt inside the window would only fail
                time.sleep(wait)
            r = w = None
            try:
                # Command port first: it is the one briefly refused while the
                # plug-in re-arms, and a half-open attempt that touched only
                # it is cleaned up by the plug-in (its close triggers the
                # rebind); a stray response-port connection would not be.
                # Nothing is sent before both are up, so no reply is missed.
                w = socket.create_connection((LR_HOST, recv_port), timeout=5)
                r = socket.create_connection((LR_HOST, send_port), timeout=5)
                r.settimeout(LR_READ_TIMEOUT)
                w.settimeout(10)
                self.reader = LineSocket(r)
                self.writer = LineSocket(w)
                self.token = _read_token()
                self.suspect = True
                _dlog("connected to Lightroom (recv %d / send %d)" % (recv_port, send_port))
                return True
            except OSError as e:
                last = e
                if w is not None:
                    # Half-open: close it now (not at garbage collection) and
                    # give the plug-in its re-arm window before the next try.
                    for sock in (r, w):
                        if sock is not None:
                            try:
                                sock.close()
                            except OSError:
                                pass
                    self.dropped_at = time.time()
                    continue
                # Retrying rides out a plug-in restart or reload (the old
                # instance writes 'stopped' a moment before the new one binds).
                # Only when 'stopped' is older than that handoff and nothing
                # listens at all (Lightroom closed) is waiting pointless.
                if (isinstance(e, ConnectionRefusedError)
                        and _handshake().get("status") != "running"
                        and _handshake_age() > HANDOFF_GRACE_S):
                    owners = port_owners((recv_port, send_port))
                    if owners is not None and not any(owners.values()):
                        break
                time.sleep(0.3)
        _dlog("could not connect to Lightroom: %s" % last)
        return False

    def close(self):
        if self.reader or self.writer:
            self.dropped_at = time.time()
        if self.reader:
            self.reader.close()
        if self.writer:
            self.writer.close()
        self.reader = None
        self.writer = None

    def _roundtrip(self, cmd, timeout):
        msg = dict(cmd)
        msg["token"] = self.token or ""
        line = json.dumps(msg, allow_nan=False)
        self.reader.sock.settimeout(timeout)
        self.writer.sendline(line)
        resp = self._read_response(cmd.get("id"))
        self.last_ok = time.time()
        return resp

    def _unreachable(self, prefix):
        d = diagnose()
        return {"ok": False, "error": "%s %s" % (prefix, d["hint"]), "diagnosis": d}

    def _probe(self, retries=1):
        """Ping with a short timeout before trusting the link. Any answer
        (even 'unauthorized') proves the plug-in loop is alive. Returns None
        when alive, else an error response. An unanswered ping or a broken
        link (dead response channel, plug-in reload, busy plug-in) gets ONE
        clean reconnect after RECONNECT_GRACE_S and a second ping."""
        self._probe_id -= 1
        try:
            self._roundtrip({"id": self._probe_id, "cmd": "ping", "params": {}}, PROBE_TIMEOUT)
            self.suspect = False
            return None
        except socket.timeout:
            _dlog("liveness probe timed out")
            failure, unresponsive = "Lightroom is not responding.", True
        except (OSError, ConnectionError) as e:
            _dlog("link error during probe (%s)" % e)
            failure, unresponsive = "Lost connection to Lightroom (%s)." % e, False
        self.close()
        if retries > 0:
            if self.connect():  # waits out the plug-in's re-arm window itself
                return self._probe(retries - 1)
        self.suspect = True
        d = diagnose(unresponsive=unresponsive)
        return {"ok": False, "error": "%s %s" % (failure, d["hint"]), "diagnosis": d}

    def request(self, cmd):
        """Send one command dict to Lr and return the matching response dict."""
        if not self.connected():
            hs = _handshake()
            if hs.get("status") == "running" and hs.get("protocol") != PROTOCOL:
                d = diagnose()
                return {"ok": False, "error": d["hint"], "diagnosis": d}
            if not self.connect():
                return self._unreachable("Lightroom not reachable.")
        if self.suspect or time.time() - self.last_ok > IDLE_PROBE_S:
            err = self._probe()
            if err:
                return err
        resp = self._exchange(cmd)
        if resp.get("errorCode") == "unauthorized":
            # The plug-in refused the line before running anything, so a
            # resend is safe -- worthwhile only if it rotated its secret
            # (a plug-in reload) since we read it.
            fresh = _read_token()
            if fresh and fresh != self.token:
                self.token = fresh
                resp = self._exchange(cmd)
        return resp

    def _exchange(self, cmd):
        # Quick commands should fail fast; renders/exports may legitimately take long.
        timeout = _lr_timeout(cmd)
        try:
            return self._roundtrip(cmd, timeout)
        except socket.timeout:
            # Still connected, just no answer: the command may be running or
            # may already have applied. Never resend it; probe next time.
            self.suspect = True
            _dlog("timeout after %ss waiting for %s" % (timeout, cmd.get("cmd")))
            return {"ok": False, "error": "Lightroom did not answer %r within %ss; it may still "
                    "be running or may already have applied -- verify state before retrying. "
                    "The next request checks liveness first." % (cmd.get("cmd"), timeout)}
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
                return self._unreachable("Lost connection to Lightroom and could not "
                                         "reconnect (%s)." % e)
            err = self._probe()
            if err:
                return err
            try:
                return self._roundtrip(cmd, timeout)
            except socket.timeout:
                self.suspect = True
                return {"ok": False, "error": "Lightroom did not answer %r within %ss after "
                        "reconnecting." % (cmd.get("cmd"), timeout)}
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
                _dlog("non-JSON from Lr: %r" % line[:300])
                continue
            if want_id is None or obj.get("id") == want_id or "id" not in obj:
                return obj
            # otherwise a stale/foreign line (e.g. a late reply); keep reading
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


def _maintenance():
    pruned = prune_previews()
    if pruned["removed"]:
        _dlog("pruned %d stale previews (%.1f MB)" % (pruned["removed"], pruned["freedBytes"] / 1e6))
    _rotate_log(DAEMON_LOG)
    return pruned


def _daemon_status(link):
    from . import __version__
    return {
        "daemon": True,
        "version": __version__,
        "protocol": PROTOCOL,
        "lrConnected": link.connected(),
        "suspect": link.suspect,
        "lastOkAgeS": round(time.time() - link.last_ok, 1) if link.last_ok else None,
        "diagnosis": diagnose(),
    }


def run_daemon():
    _ensure_private_dir()
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
    _maintenance()
    last_maintenance = time.time()

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
                    resp = {"ok": True, "result": _daemon_status(link)}
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
            if time.time() - last_maintenance > MAINTENANCE_EVERY_S:
                last_maintenance = time.time()
                try:
                    _maintenance()
                except Exception as e:
                    _dlog("maintenance failed: %s" % e)
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
    _ensure_private_dir()
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
            _rotate_log(DAEMON_LOG)
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
        return {"ok": True, "result": {"daemon": False, "diagnosis": diagnose()}}
    return _send_ctl("ping")
