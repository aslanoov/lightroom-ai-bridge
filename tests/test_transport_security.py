"""Wire protocol json-1: strict JSON parsing, token auth, liveness probes,
port diagnosis and preview/log housekeeping. No Lightroom required.

Run: .venv/bin/python -m unittest discover -s tests -v
"""
import json
import os
import socket
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from lupa.lua51 import LuaRuntime

from lightroom_bridge import _core
from test_bridge_upgrade import FIXTURE

ROOT = Path(__file__).resolve().parents[1]
SOURCE = (ROOT / 'ClaudeBridge.lrplugin/Bridge.lua').read_text()


def load_dispatch():
    """Bridge.lua up to the socket server, with a test handler and a token setter."""
    lua = LuaRuntime(unpack_returned_tuples=True)
    lua.execute(FIXTURE + r'''
    function cat:getTargetPhoto() return nil end
    local baseImport = import
    function import(name)
        if name == 'LrPathUtils' then return {getStandardFilePath=function() return '/nonexistent' end,
            child=function(a, b) return a .. '/' .. b end} end
        if name == 'LrApplication' then return {activeCatalog=function() return cat end,
            versionString=function() return '15.6' end} end
        local ok, value = pcall(baseImport, name); if ok then return value end; return {}
    end
    function require(name) return {VERSION={major=1,minor=5,revision=0}} end
    ''')
    source = SOURCE.split('-- Socket server + main loop', 1)[0]
    exports = lua.execute(source + r'''
    H.echo = function(p) calls.echo = (calls.echo or 0) + 1; return {got = p.value} end
    return {decode = jsonDecode, handle = handleLine,
            setToken = function(t) AUTH_TOKEN = t end}
    ''')
    return lua, exports


class JsonDecoderTests(unittest.TestCase):
    def setUp(self):
        self.lua, self.x = load_dispatch()

    def decode(self, text):
        value, err = self.x.decode(text)
        return value, err

    def test_round_trips_python_json(self):
        payload = {"id": 7, "cmd": "set", "params": {"Exposure": -0.35, "Big": 1e-7,
                   "Curve": [0, 0, 128, 140, 255, 255], "Name": 'Ünï "q" \\ / \t',
                   "Emoji": "\U0001F4F7", "On": True, "Off": False}}
        value, err = self.decode(json.dumps(payload))
        self.assertIsNone(err)
        p = value.params
        self.assertEqual(value.cmd, "set")
        self.assertAlmostEqual(p.Exposure, -0.35)
        self.assertAlmostEqual(p.Big, 1e-7)
        self.assertEqual(list(p.Curve.values()), [0, 0, 128, 140, 255, 255])
        self.assertEqual(p.Name, 'Ünï "q" \\ / \t')   # lupa decodes the UTF-8 bytes
        self.assertEqual(p.Emoji, "\U0001F4F7")
        self.assertIs(p.On, True)
        self.assertIs(p.Off, False)

    def test_null_means_absent(self):
        value, err = self.decode('{"cmd":"x","params":{"flag":null,"keep":1}}')
        self.assertIsNone(err)
        self.assertIsNone(value.params.flag)
        self.assertEqual(value.params.keep, 1)

    def test_lua_code_is_never_evaluated(self):
        self.lua.execute('pwned = false')
        for text in ('{id=1,cmd="ping"}',
                     '{id=1,cmd=(function() pwned=true; return "ping" end)()}',
                     '{"id":1,"cmd":"ping"} pwned=true',
                     '{"id":1,"cmd":"ping"}{"x":1}'):
            value, err = self.decode(text)
            self.assertIsNone(value, text)
            self.assertTrue(err, text)
        self.assertFalse(self.lua.globals().pwned)

    def test_rejects_malformed_values(self):
        for text in ('{"a":01}', '{"a":1.}', '{"a":-}', '{"a":1e999}', '{"a":NaN}',
                     '{"a":"\\x"}', '{"a":"\\ud800"}', '{"a":"line\nbreak"}', '{"a":1,}',
                     '{"a" 1}', '{"a":[1 2]}', '"unterminated', ''):
            value, err = self.decode(text)
            self.assertTrue(err, text)

    def test_nesting_is_bounded(self):
        _, err = self.decode('[' * 65 + ']' * 65)
        self.assertIn('nesting', err)
        value, err = self.decode('[' * 60 + ']' * 60)
        self.assertIsNone(err)


class DispatchAuthTests(unittest.TestCase):
    def setUp(self):
        self.lua, self.x = load_dispatch()
        self.g = self.lua.globals()

    def handle(self, obj_or_text):
        text = obj_or_text if isinstance(obj_or_text, str) else json.dumps(obj_or_text)
        return json.loads(self.x.handle(text))

    def test_no_command_runs_before_a_token_exists(self):
        r = self.handle({"id": 1, "cmd": "echo", "params": {"value": 1}, "token": ""})
        self.assertEqual(r["errorCode"], "unauthorized")
        self.assertIsNone(self.g.calls.echo)

    def test_wrong_or_missing_token_is_refused_without_running(self):
        self.x.setToken("a" * 64)
        for token in (None, "", "b" * 64, "a" * 63, 42):
            msg = {"id": 2, "cmd": "echo", "params": {"value": 1}}
            if token is not None:
                msg["token"] = token
            r = self.handle(msg)
            self.assertEqual(r["errorCode"], "unauthorized", token)
            self.assertEqual(r["id"], 2)
            self.assertNotIn("unknown command", r["error"])
        self.assertIsNone(self.g.calls.echo)

    def test_valid_token_runs_handler(self):
        self.x.setToken("a" * 64)
        r = self.handle({"id": 3, "cmd": "echo", "params": {"value": 5}, "token": "a" * 64})
        self.assertTrue(r["ok"])
        self.assertEqual(r["result"]["got"], 5)
        self.assertEqual(self.g.calls.echo, 1)

    def test_old_lua_literal_client_gets_actionable_parse_error(self):
        self.x.setToken("a" * 64)
        r = self.handle('{id=4,cmd="echo",params={value=1}}')
        self.assertEqual(r["errorCode"], "parse")
        self.assertIn("lrc daemon stop", r["error"])
        self.assertIsNone(self.g.calls.echo)

    def test_params_must_be_an_object(self):
        self.x.setToken("a" * 64)
        r = self.handle({"id": 5, "cmd": "echo", "params": "x", "token": "a" * 64})
        self.assertFalse(r["ok"])
        self.assertIsNone(self.g.calls.echo)


class FakePlugin:
    """Two localhost listeners speaking json-1, like Bridge.lua. Serves one
    client pair at a time; `stale_pairs` first pairs are accepted but never
    answered (a dead response channel), `ignore` commands never get a reply."""

    def __init__(self, token, respond=True, stale_pairs=0, ignore=(), stale_window=0.0):
        self.token, self.respond, self.seen, self.conns = token, respond, [], []
        self.stale_pairs, self.ignore = stale_pairs, set(ignore)
        # Like Bridge.lua's send port: a pair opened within `stale_window`
        # seconds after the previous client left lands on the old listener
        # (before the plug-in rebinds it) and is never answered.
        self.stale_window, self.closed_at, self.stale = stale_window, None, set()
        self.recv_srv = socket.create_server(("127.0.0.1", 0))
        self.send_srv = socket.create_server(("127.0.0.1", 0))
        self.ports = (self.recv_srv.getsockname()[1], self.send_srv.getsockname()[1])
        threading.Thread(target=self._run, daemon=True).start()

    def _run(self):
        pair = 0
        while True:
            try:
                out, _ = self.send_srv.accept()
                inp, _ = self.recv_srv.accept()
            except OSError:
                return
            pair += 1
            if self.closed_at is not None and time.time() - self.closed_at < self.stale_window:
                self.stale.add(pair)
            reader = inp.makefile("r", encoding="utf-8")
            self.conns += [out, inp, reader]
            try:
                for line in reader:
                    msg = json.loads(line)
                    self.seen.append((pair, msg))
                    if (not self.respond or pair <= self.stale_pairs or pair in self.stale
                            or msg["cmd"] in self.ignore):
                        continue
                    if msg.get("token") != self.token:
                        resp = {"id": msg.get("id"), "ok": False, "errorCode": "unauthorized",
                                "error": "unauthorized"}
                    else:
                        resp = {"id": msg.get("id"), "ok": True, "result": {"cmd": msg["cmd"]}}
                    out.sendall((json.dumps(resp) + "\n").encode())
            except OSError:
                pass
            self.closed_at = time.time()

    def cmds(self):
        return [(pair, m["cmd"]) for pair, m in self.seen]

    def close(self):
        for s in self.conns + [self.recv_srv, self.send_srv]:
            try:
                s.close()
            except OSError:
                pass


class LinkTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        d = self.tmp.name
        self.auth = os.path.join(d, "auth.token")
        self.handshake = os.path.join(d, "bridge.json")
        self.patches = [
            patch.object(_core, "AUTH_PATH", self.auth),
            patch.object(_core, "HANDSHAKE", self.handshake),
            patch.object(_core, "DAEMON_LOG", os.path.join(d, "daemon.log")),
            patch.object(_core, "PROBE_TIMEOUT", 0.5),
            patch.object(_core, "RECONNECT_GRACE_S", 0.5),
            patch.object(_core, "port_owners", lambda ports: {p: [] for p in ports}),
        ]
        for p in self.patches:
            p.start()
        self.plugin = None
        self.link = None

    def tearDown(self):
        for p in self.patches:
            p.stop()
        if self.link:
            self.link.close()
        if self.plugin:
            self.plugin.close()
        self.tmp.cleanup()

    def start(self, token="t1", respond=True, file_token=None, **kw):
        Path(self.auth).write_text(file_token or token)
        self.plugin = FakePlugin(token, respond, **kw)
        ports = self.plugin.ports
        self.patches.append(patch.object(_core, "ports", lambda: ports))
        self.patches[-1].start()
        self.link = _core.LrLink()
        return self.link

    def test_sends_json_with_token_after_a_probe(self):
        link = self.start()
        r = link.request({"id": 10, "cmd": "get_settings", "params": {"x": [1, 2]}})
        self.assertEqual(r, {"id": 10, "ok": True, "result": {"cmd": "get_settings"}})
        (_, probe), (_, real) = self.plugin.seen
        self.assertEqual(probe["cmd"], "ping")
        self.assertLess(probe["id"], 0)
        self.assertEqual(real["token"], "t1")
        self.assertEqual(real["params"], {"x": [1, 2]})
        link.request({"id": 11, "cmd": "get_settings"})
        self.assertEqual(len(self.plugin.seen), 3, "no re-probe while the link is fresh")

    def test_unresponsive_plugin_fails_fast_with_diagnosis(self):
        link = self.start(respond=False)
        t0 = time.time()
        r = link.request({"id": 12, "cmd": "set", "params": {}})
        self.assertLess(time.time() - t0, 5)
        self.assertFalse(r["ok"])
        self.assertIn("not responding", r["error"])
        self.assertIn("diagnosis", r)
        self.assertFalse(link.connected(), "a dead channel is dropped for a clean reconnect")
        self.assertNotIn("set", [c for _, c in self.plugin.cmds()],
                         "the real command must not be sent to a silent plug-in")

    def test_stale_response_channel_heals_by_reconnecting(self):
        link = self.start(stale_pairs=1)
        r = link.request({"id": 15, "cmd": "set", "params": {}})
        self.assertTrue(r["ok"], r)
        self.assertEqual(self.plugin.cmds(), [(1, "ping"), (2, "ping"), (2, "set")])

    def test_reconnect_waits_for_the_plugin_to_rebind(self):
        # The first pair goes silent; an immediate reconnect would land on the
        # not-yet-rebound send listener. The retry must wait for the rebind.
        link = self.start(stale_pairs=1, stale_window=0.3)
        r = link.request({"id": 18, "cmd": "set", "params": {}})
        self.assertTrue(r["ok"], r)
        self.assertNotIn(2, self.plugin.stale, "reconnected inside the stale window")

    def test_command_timeout_keeps_link_and_never_resends(self):
        link = self.start(ignore={"slow_write"})
        with patch.object(_core, "_lr_timeout", lambda cmd: 0.5):
            r = link.request({"id": 16, "cmd": "slow_write", "params": {}})
        self.assertFalse(r["ok"])
        self.assertIn("may already have applied", r["error"])
        self.assertTrue(link.connected(), "the reply may still come: no client turnover")
        self.assertTrue(link.suspect)
        self.assertEqual([c for _, c in self.plugin.cmds()].count("slow_write"), 1)
        r = link.request({"id": 17, "cmd": "get_settings"})
        self.assertTrue(r["ok"])
        self.assertEqual(self.plugin.cmds()[-2:], [(1, "ping"), (1, "get_settings")],
                         "a suspect link is probed before the next command")

    def test_rotated_token_is_reread_once(self):
        link = self.start(token="new", file_token="old")
        link.connect()                      # reads "old"
        Path(self.auth).write_text("new")   # the plug-in reloaded meanwhile
        r = link.request({"id": 13, "cmd": "set", "params": {}})
        self.assertTrue(r["ok"])
        self.assertEqual([m["token"] for _, m in self.plugin.seen if m["cmd"] == "set"], ["old", "new"])

    def test_old_plugin_protocol_is_refused_before_connecting(self):
        Path(self.handshake).write_text(json.dumps({"status": "running", "plugin": "1.4.0"}))
        link = _core.LrLink()
        with patch.object(link, "connect") as connect:
            r = link.request({"id": 14, "cmd": "ping"})
        connect.assert_not_called()
        self.assertIn("predates protocol", r["error"])


class ConnectTests(unittest.TestCase):
    """Fail fast when Lightroom is closed, but ride out a plug-in reload."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.hs = os.path.join(self.tmp.name, "bridge.json")
        free = []
        for _ in range(2):
            s = socket.socket()
            s.bind(("127.0.0.1", 0))
            free.append(s.getsockname()[1])
            s.close()
        self.patches = [patch.object(_core, "HANDSHAKE", self.hs),
                        patch.object(_core, "DAEMON_LOG", os.path.join(self.tmp.name, "d.log")),
                        patch.object(_core, "ports", lambda: tuple(free)),
                        patch.object(_core, "port_owners", lambda ports: {p: [] for p in ports})]
        for p in self.patches:
            p.start()
        Path(self.hs).write_text(json.dumps({"status": "stopped", "protocol": _core.PROTOCOL}))

    def tearDown(self):
        for p in self.patches:
            p.stop()
        self.tmp.cleanup()

    def elapsed_connect(self):
        t0 = time.time()
        self.assertFalse(_core.LrLink().connect(timeout=1.0))
        return time.time() - t0

    def test_lightroom_closed_fails_fast(self):
        old = time.time() - 120
        os.utime(self.hs, (old, old))
        self.assertLess(self.elapsed_connect(), 0.3)

    def test_fresh_stop_is_a_reload_and_keeps_retrying(self):
        self.assertGreater(self.elapsed_connect(), 0.9)


class ConnectOrderTests(unittest.TestCase):
    """Half-open attempts are closed at once and never touch the response port."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        hs = os.path.join(self.tmp.name, "bridge.json")
        Path(hs).write_text(json.dumps({"status": "running", "protocol": _core.PROTOCOL}))
        self.listener = socket.create_server(("127.0.0.1", 0))
        self.listener.settimeout(0.3)
        dead = socket.socket()
        dead.bind(("127.0.0.1", 0))
        self.dead_port = dead.getsockname()[1]
        dead.close()
        self.live_port = self.listener.getsockname()[1]
        self.patches = [patch.object(_core, "HANDSHAKE", hs),
                        patch.object(_core, "DAEMON_LOG", os.path.join(self.tmp.name, "d.log")),
                        patch.object(_core, "RECONNECT_GRACE_S", 0.4)]
        for p in self.patches:
            p.start()

    def tearDown(self):
        for p in self.patches:
            p.stop()
        self.listener.close()
        self.tmp.cleanup()

    def test_refused_command_port_never_opens_the_response_port(self):
        with patch.object(_core, "ports", lambda: (self.dead_port, self.live_port)):
            self.assertFalse(_core.LrLink().connect(timeout=0.5))
        with self.assertRaises(socket.timeout):
            self.listener.accept()

    def test_half_open_command_socket_is_closed_and_retry_waits(self):
        with patch.object(_core, "ports", lambda: (self.live_port, self.dead_port)):
            self.assertFalse(_core.LrLink().connect(timeout=1.0))
        conn, _ = self.listener.accept()
        conn.settimeout(1)
        self.assertEqual(conn.recv(1), b"", "the half-open command socket must be closed")
        conn.close()

    def test_no_attempt_inside_the_rearm_window(self):
        calls = []
        real = socket.create_connection

        def spy(addr, *a, **k):
            calls.append((time.time(), addr[1]))
            return real(addr, *a, **k)

        link = _core.LrLink()
        t0 = link.dropped_at = time.time()
        with patch.object(_core, "ports", lambda: (self.live_port, self.dead_port)), \
                patch.object(_core.socket, "create_connection", spy):
            link.connect(timeout=1.5)
        for c in range(3):
            try:
                self.listener.accept()[0].close()
            except socket.timeout:
                break
        command_tries = [t - t0 for t, port in calls if port == self.live_port]
        self.assertTrue(command_tries)
        self.assertGreaterEqual(command_tries[0], 0.4, "first try waits out the drop")
        gaps = [b - a for a, b in zip(command_tries, command_tries[1:])]
        self.assertTrue(all(g >= 0.4 for g in gaps), gaps)


class DiagnoseTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.hs = os.path.join(self.tmp.name, "bridge.json")
        self.p = patch.object(_core, "HANDSHAKE", self.hs)
        self.p.start()

    def tearDown(self):
        self.p.stop()
        self.tmp.cleanup()

    def hint(self, owners, **kw):
        return _core.diagnose(owners=owners, **kw)["hint"]

    def test_hints(self):
        lr = [{"pid": 1, "command": "Adobe Lightroom Classic"}]
        self.assertIn("Nothing is listening", self.hint({49463: [], 49464: []}))
        self.assertIn("held by MIDI2LR (pid 9)",
                      self.hint({49463: [{"pid": 9, "command": "MIDI2LR"}], 49464: []}))
        self.assertIn("only one bridge port", self.hint({49463: lr, 49464: []}))
        self.assertIn("did not answer a ping", self.hint({49463: lr, 49464: lr}, unresponsive=True))
        self.assertIn("listening on both", self.hint({49463: lr, 49464: lr}))
        with patch.object(_core, "port_owners", lambda ports: None):
            self.assertIn("lsof unavailable", _core.diagnose()["hint"])
        Path(self.hs).write_text(json.dumps({"status": "running", "plugin": "1.4.0"}))
        self.assertIn("predates protocol", self.hint({49463: lr, 49464: lr}))


class HousekeepingTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = self.tmp.name

    def tearDown(self):
        self.tmp.cleanup()

    def make(self, name, age_s, size=1000, now=1_000_000):
        path = os.path.join(self.root, name)
        if name.startswith("geometry-"):
            os.makedirs(path)
            Path(path, "x.jpg").write_bytes(b"x" * size)
        else:
            Path(path).write_bytes(b"x" * size)
        os.utime(path, (now - age_s, now - age_s))
        return path

    def test_prunes_only_stale_renders(self):
        now = 1_000_000
        old = self.make("A1_claude.jpg", 2 * 86400)
        fresh = self.make("A2_claude.jpg", 3600)
        geo = self.make("geometry-1.5", 2 * 86400)
        other = self.make("notes.txt", 9 * 86400)
        r = _core.prune_previews(root=self.root, now=now)
        self.assertEqual(r["removed"], 2)
        self.assertFalse(os.path.exists(old))
        self.assertFalse(os.path.exists(geo))
        self.assertTrue(os.path.exists(fresh))
        self.assertTrue(os.path.exists(other), "unknown files are never touched")

    def test_size_cap_evicts_oldest_but_spares_in_flight(self):
        now = 1_000_000
        a = self.make("a.jpg", 7200, size=600)
        b = self.make("b.jpg", 3600, size=600)
        c = self.make("c.jpg", 60, size=600)  # younger than min_age
        r = _core.prune_previews(root=self.root, now=now, max_bytes=1000)
        self.assertFalse(os.path.exists(a))
        self.assertFalse(os.path.exists(b))
        self.assertTrue(os.path.exists(c))
        self.assertEqual(r["keptBytes"], 600)

    def test_missing_dir_is_harmless(self):
        r = _core.prune_previews(root=os.path.join(self.root, "nope"))
        self.assertEqual(r["removed"], 0)

    def test_log_rotation_keeps_one_generation(self):
        log = os.path.join(self.root, "x.log")
        Path(log).write_bytes(b"a" * 20)
        _core._rotate_log(log, max_bytes=10)
        self.assertFalse(os.path.exists(log))
        self.assertEqual(Path(log + ".1").read_bytes(), b"a" * 20)
        Path(log).write_bytes(b"b" * 5)
        _core._rotate_log(log, max_bytes=10)
        self.assertTrue(os.path.exists(log))


if __name__ == "__main__":
    unittest.main()
