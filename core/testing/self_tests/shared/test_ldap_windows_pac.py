import errno
import socket
import ssl
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from threading import Thread
from types import SimpleNamespace

import pytest
from ldap3 import NONE, SIMPLE, Connection, Server
from ldap3.core.exceptions import LDAPSocketOpenError, communication_exception_factory

from core.authentication import ldap


@pytest.fixture(autouse=True)
def isolated_logs(monkeypatch, tmp_path):
    monkeypatch.setenv("SMARTTEST_LOG_DIR", str(tmp_path))


@pytest.fixture
def windows(monkeypatch):
    monkeypatch.setattr(ldap, "sys_platform", "win32")
    waits = []
    monkeypatch.setattr(ldap.time, "sleep", waits.append)
    return waits


def fake_auth(outcomes, *, windows_pac=True, platform="client"):
    connections = []
    servers = []

    def server_factory(host, **options):
        server = SimpleNamespace(host=host, port=389, ssl=False, options=options,
                                 candidate_addresses=lambda: ["address"])
        servers.append(server)
        return server

    def connection_factory(server, **options):
        outcome = outcomes[len(connections)]
        connection = SimpleNamespace(server=server, options=options, bound=False, socket=None,
                                     result={"result": 49, "description": "invalidCredentials"}, closed=False)

        def bind():
            if isinstance(outcome, Exception):
                raise outcome
            connection.bound = bool(outcome)
            return outcome

        def unbind():
            connection.closed = True

        connection.bind = bind
        connection.unbind = unbind
        connections.append(connection)
        return connection

    auth = ldap.LdapAuthenticator(server_factory=server_factory, connection_factory=connection_factory,
                                  authentication=SIMPLE, get_info=NONE, subtree=None,
                                  windows_pac=windows_pac, platform=platform)
    return auth, connections, servers


def test_direct_success_stops_without_pac(windows):
    auth, connections, servers = fake_auth([True])
    assert auth.authenticate("alice", "secret")["success"]
    assert len(connections) == 1
    assert windows == []
    assert servers[0].options["connect_timeout"] == 5
    assert connections[0].options["receive_timeout"] == 5
    assert connections[0].closed


def test_two_network_failures_then_direct_success(windows):
    error = communication_exception_factory(LDAPSocketOpenError, ConnectionRefusedError())("refused")
    auth, connections, _ = fake_auth([error, socket.gaierror(), True])
    assert auth.authenticate("alice", "secret")["success"]
    assert len(connections) == 3
    assert windows == [2, 2]
    assert all(c.closed for c in connections)


@pytest.mark.parametrize("outcome,code", [
    (False, "invalid_credentials"),
    (ValueError("protocol error"), "ldap_unavailable"),
    (ssl.SSLCertVerificationError("bad certificate"), "ldap_unavailable"),
    (LDAPSocketOpenError("configuration error"), "ldap_unavailable"),
])
def test_non_network_failure_does_not_retry(windows, outcome, code):
    auth, connections, _ = fake_auth([outcome])
    assert auth.authenticate("alice", "secret")["code"] == code
    assert len(connections) == 1
    assert windows == []


def test_identity_failure_after_bind_does_not_retry(windows):
    auth, connections, _ = fake_auth([True])
    auth._identity = lambda *_: (_ for _ in ()).throw(ConnectionResetError())
    assert auth.authenticate("alice", "secret")["code"] == "ldap_unavailable"
    assert len(connections) == 1
    assert windows == []


def test_noncredential_bind_rejection_is_not_password_error(windows):
    auth, connections, _ = fake_auth([False])
    factory = auth._connection

    def connection_factory(*args, **kwargs):
        connection = factory(*args, **kwargs)
        connection.result = {"result": 2, "description": "protocolError"}
        return connection

    auth._connection = connection_factory
    assert auth.authenticate("alice", "secret")["code"] == "ldap_unavailable"
    assert len(connections) == 1
    assert windows == []


def test_missing_optional_pac_dependency_returns_failure(windows, monkeypatch):
    import builtins
    original_import = builtins.__import__

    def import_without_pac(name, *args, **kwargs):
        if name == "windows_pac":
            raise ModuleNotFoundError("No module named pypac")
        return original_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", import_without_pac)
    auth, connections, _ = fake_auth([ConnectionRefusedError()] * 3 + [True])
    assert auth.authenticate("alice", "secret")["detail"] == "ModuleNotFoundError"
    assert len(connections) == 4
    assert all(c.closed for c in connections)


def test_cleanup_socket_error_does_not_replace_authentication_result(windows):
    auth, connections, _ = fake_auth([True])
    factory = auth._connection

    def connection_factory(*args, **kwargs):
        connection = factory(*args, **kwargs)
        connection.socket = SimpleNamespace(close=lambda: (_ for _ in ()).throw(OSError("close failed")))
        return connection

    auth._connection = connection_factory
    assert auth.authenticate("alice", "secret")["success"]
    assert connections[0].closed


@pytest.mark.parametrize("system,platform", [("linux", "client"), ("darwin", "client"), ("win32", "web")])
def test_other_platforms_keep_single_attempt(monkeypatch, system, platform):
    monkeypatch.setattr(ldap, "sys_platform", system)
    auth, connections, servers = fake_auth([ConnectionRefusedError()], platform=platform)
    assert auth.authenticate("alice", "secret")["code"] == "ldap_unavailable"
    assert len(connections) == 1
    assert connections[0].options.keys() == {"user", "password", "authentication"}
    assert servers[0].options == {"get_info": NONE}


def test_network_classification_keeps_tls_and_local_errors_out():
    network = communication_exception_factory(LDAPSocketOpenError, TimeoutError())("timeout")
    tls = communication_exception_factory(LDAPSocketOpenError, ssl.SSLError())("TLS")
    assert ldap._network_failure(LDAPSocketOpenError("all failed", [(network, ("host", 389))]))
    assert not ldap._network_failure(LDAPSocketOpenError("mixed", [(network, ("h", 389)), (tls, ("h", 389))]))
    assert not ldap._network_failure(tls)
    assert not ldap._network_failure(OSError(errno.EACCES, "denied"))
    windows_error = OSError("network unreachable")
    windows_error.winerror = 10051
    assert ldap._network_failure(windows_error)


def pac_module():
    # These are Windows-only application dependencies; allow other-platform CI
    # to run the owner tests above without installing the native JS runtime.
    pytest.importorskip("pypac")
    pytest.importorskip("python_socks")
    from core.authentication import windows_pac
    return windows_pac


@pytest.mark.parametrize("route", [None, "DIRECT; SOCKS5 localhost:1080", "PROXY localhost:8080", "SOCKS localhost:1080", "SOCKS4 localhost:1080", ""])
def test_pac_missing_or_first_route_unsupported_is_reported(windows, monkeypatch, route):
    module = pac_module()
    pac = None if route is None else SimpleNamespace(find_proxy_for_url=lambda *_: route)
    monkeypatch.setattr(module, "get_pac", lambda **_: pac)
    auth, connections, _ = fake_auth([ConnectionRefusedError()] * 3 + [True])
    result = auth.authenticate("alice", "secret")
    assert result["detail"] == ("pac_unavailable" if route is None else "pac_route_not_socks5")
    assert not result["success"]
    assert len(connections) == 4
    assert all(c.closed for c in connections)
    assert windows == [2, 2]


def test_pac_script_failure_is_not_hidden(windows, monkeypatch):
    module = pac_module()
    from pypac.parser import PACFile
    pac = PACFile('function FindProxyForURL(url, host) { if (host !== "0.0.0.0") throw new Error("bad"); return "DIRECT"; }')
    monkeypatch.setattr(module, "get_pac", lambda **_: pac)
    auth, connections, _ = fake_auth([ConnectionRefusedError()] * 3 + [True])
    result = auth.authenticate("alice", "secret")
    assert result["code"] == "ldap_unavailable"
    assert result["detail"] == "pac_script_failed"
    assert len(connections) == 4


def recv_exact(sock, count):
    result = b""
    while len(result) < count:
        data = sock.recv(count - len(result))
        if not data:
            raise EOFError("peer closed")
        result += data
    return result


def recv_ldap(sock):
    tag, length = recv_exact(sock, 2)
    if length & 0x80:
        length = int.from_bytes(recv_exact(sock, length & 0x7f), "big")
    return tag, recv_exact(sock, length)


@contextmanager
def socks_ldap_server(*, reject=False):
    """A bounded wire peer verifies actual SOCKS5 negotiation and LDAP bytes."""
    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    listener.listen()
    listener.settimeout(5)
    observed = {}
    errors = []

    def serve():
        try:
            with listener.accept()[0] as peer:
                peer.settimeout(5)
                version, methods = recv_exact(peer, 2)
                assert version == 5 and b"\x00" in recv_exact(peer, methods)
                peer.sendall(b"\x05\x00")
                assert recv_exact(peer, 4) == b"\x05\x01\x00\x03"
                hostname = recv_exact(peer, recv_exact(peer, 1)[0]).decode()
                port = int.from_bytes(recv_exact(peer, 2), "big")
                observed["target"] = hostname, port
                peer.sendall(bytes([5, 5 if reject else 0, 0, 1, 127, 0, 0, 1, 0, 0]))
                if reject:
                    try:
                        observed["closed"] = peer.recv(1) == b""
                    except ConnectionResetError:
                        observed["closed"] = True
                    return
                tag, bind = recv_ldap(peer)
                assert tag == 0x30 and b"alice" in bind and b"secret" in bind
                observed["bind"] = True
                message_id = bind[:3]  # first message is short INTEGER id 1
                peer.sendall(b"\x30\x0c" + message_id + b"\x61\x07\x0a\x01\x00\x04\x00\x04\x00")
                recv_ldap(peer)  # LDAP unbind
                observed["closed"] = peer.recv(1) == b""
        except Exception as error:
            errors.append(error)

    worker = Thread(target=serve, daemon=True)
    worker.start()
    try:
        yield listener.getsockname()[1], observed
    finally:
        worker.join(7)
        listener.close()
        assert not worker.is_alive(), "wire peer did not finish"
        assert not errors, errors


@pytest.mark.parametrize("reject", [False, True])
def test_real_socks5_ldap_connection_after_three_dns_failures(windows, monkeypatch, reject):
    module = pac_module()
    from pypac.parser import PACFile
    original_getaddrinfo = socket.getaddrinfo
    original_socket = socket.socket
    phase = {"pac": False, "direct_dns": 0}

    def getaddrinfo(host, *args, **kwargs):
        if host == "ldap.invalid":
            assert not phase["pac"], "LDAP hostname was resolved locally in SOCKS phase"
            phase["direct_dns"] += 1
            raise socket.gaierror(socket.EAI_NONAME, "not on local DNS")
        return original_getaddrinfo(host, *args, **kwargs)

    monkeypatch.setattr(socket, "getaddrinfo", getaddrinfo)
    with socks_ldap_server(reject=reject) as (port, observed):
        pac = PACFile(f'''function FindProxyForURL(url, host) {{
            if (host !== "0.0.0.0" && (host !== "ldap.invalid" || url !== "ldap://ldap.invalid:389"))
                throw new Error("incorrect LDAP destination");
            return "SOCKS5 127.0.0.1:{port}";
        }}''')

        def get_pac(**options):
            assert options == {"from_os_settings": True, "from_dns": False, "timeout": 2}
            phase["pac"] = True
            return pac

        monkeypatch.setattr(module, "get_pac", get_pac)
        auth = ldap.LdapAuthenticator(host="ldap.invalid", authentication=SIMPLE,
                                      get_info=NONE, subtree=None, windows_pac=True)
        result = auth.authenticate("alice", "secret")
    assert result["success"] is not reject
    assert observed["target"] == ("ldap.invalid", 389)
    assert observed["closed"]
    assert phase["direct_dns"] >= 3
    if reject:
        assert result["detail"] == "socks_connection_failed"
    assert windows == [2, 2]
    assert socket.socket is original_socket


def test_concurrent_proxy_connections_keep_target_and_socket_isolated(monkeypatch):
    module = pac_module()
    original_socket = socket.socket
    with socks_ldap_server() as (port1, first), socks_ldap_server() as (port2, second):
        routes = {"first.invalid": port1, "second.invalid": port2}
        pac = SimpleNamespace(find_proxy_for_url=lambda url, host: f"SOCKS5 127.0.0.1:{routes[host]}")
        monkeypatch.setattr(module, "get_pac", lambda **_: pac)

        def connect(host):
            connection = Connection(Server(host, get_info=NONE), user="alice", password="secret", authentication=SIMPLE)
            module.configure_connection(connection, connect_timeout=5, receive_timeout=5, pac_timeout=2)
            try:
                return connection.bind()
            finally:
                connection.unbind()

        with ThreadPoolExecutor(max_workers=2) as pool:
            assert list(pool.map(connect, routes)) == [True, True]
    assert first["target"] == ("first.invalid", 389)
    assert second["target"] == ("second.invalid", 389)
    assert first["closed"] and second["closed"]
    assert socket.socket is original_socket


def test_pac_packaging_dependencies_and_data():
    pac_module()
    import importlib
    import runpy
    from pathlib import Path
    from client.packaging.tool_runtime_dependencies import WINDOWS_PAC_RUNTIME_MODULES, TOOL_HIDDEN_IMPORTS, TOOL_SMOKE_MODULES

    for module in WINDOWS_PAC_RUNTIME_MODULES:
        importlib.import_module(module)
        assert module in TOOL_HIDDEN_IMPORTS and module in TOOL_SMOKE_MODULES
    root = Path(__file__).resolve().parents[4]
    hook = runpy.run_path(str(root / "client/packaging/pyinstaller/hooks/hook-publicsuffixlist.py"))
    assert any(Path(source).name == "public_suffix_list.dat" and Path(source).is_file()
               for source, target in hook["datas"])
