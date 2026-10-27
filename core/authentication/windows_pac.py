"""Connection-local PAC/SOCKS5 adaptation for the pinned ldap3 runtime."""

import socket

from pypac import get_pac
from pypac.parser import MalformedPacError, proxy_url
from dukpy import JSRuntimeError
from python_socks import ProxyError, ProxyConnectionError, ProxyTimeoutError
from python_socks.sync import Proxy

from .ldap import WindowsPacError


def configure_connection(connection, *, connect_timeout, receive_timeout, pac_timeout):
    server = connection.server
    url = f"{'ldaps' if server.ssl else 'ldap'}://{server.host}:{server.port}"
    try:
        pac = get_pac(from_os_settings=True, from_dns=False, timeout=pac_timeout)
    except MalformedPacError as error:
        raise WindowsPacError("pac_script_failed") from error
    if pac is None:
        raise WindowsPacError("pac_unavailable")
    try:
        first = pac.find_proxy_for_url(url, server.host).split(";", 1)[0].strip()
    except JSRuntimeError as error:
        raise WindowsPacError("pac_script_failed") from error
    if not first or first.split()[0].upper() != "SOCKS5":
        raise WindowsPacError("pac_route_not_socks5")
    try:
        proxy = Proxy.from_url(proxy_url(first), rdns=True)
    except ValueError as error:
        raise WindowsPacError("pac_route_invalid") from error

    def open_socket(_address, use_ssl=False, unix_socket=False):
        try:
            connection.socket = proxy.connect(dest_host=server.host, dest_port=server.port,
                                              timeout=connect_timeout)
        except (ProxyError, ProxyConnectionError, ProxyTimeoutError) as error:
            raise WindowsPacError("socks_connection_failed") from error
        connection.socket.settimeout(receive_timeout)
        if use_ssl:
            server.tls.wrap_socket(connection, do_handshake=True)
        if connection.usage:
            connection._usage.open_sockets += 1
            if use_ssl:
                connection._usage.wrapped_sockets += 1
        connection.closed = False

    # ldap3 2.9.1 exposes no socket_factory. Change only this short-lived
    # server/strategy instance; retain its open/listen/bind/close lifecycle.
    # Supplying the hostname here avoids ldap3's pre-proxy getaddrinfo call.
    server.candidate_addresses = lambda: [
        (socket.AF_INET, socket.SOCK_STREAM, socket.IPPROTO_TCP, "", (server.host, server.port))
    ]
    connection.strategy._open_socket = open_socket
