from __future__ import annotations

import ipaddress
import socket
from dataclasses import dataclass
from typing import Callable, Iterable
from urllib.parse import SplitResult, urlsplit, urlunsplit

from job_platform.errors import JobValidationError

Resolver = Callable[[str, int], Iterable[str]]


@dataclass(frozen=True, slots=True)
class ResolvedTarget:
    url: str
    hostname: str
    port: int
    addresses: tuple[str, ...]


def system_resolver(hostname: str, port: int) -> tuple[str, ...]:
    return tuple(
        sorted(
            {
                item[4][0]
                for item in socket.getaddrinfo(
                    hostname, port, type=socket.SOCK_STREAM, proto=socket.IPPROTO_TCP
                )
            }
        )
    )


class UrlPolicy:
    def __init__(self, resolver: Resolver = system_resolver) -> None:
        self.resolver = resolver

    def validate(self, value: str) -> ResolvedTarget:
        raw = str(value or "")
        if (
            not raw
            or len(raw) > 2048
            or "\\" in raw
            or any(ord(character) < 32 or ord(character) == 127 for character in raw)
        ):
            raise JobValidationError("target URL is invalid")
        try:
            parsed = urlsplit(raw)
            port = parsed.port
        except ValueError as error:
            raise JobValidationError("target URL is invalid") from error
        if (
            parsed.scheme.casefold() not in {"http", "https"}
            or not parsed.hostname
            or parsed.username is not None
            or parsed.password is not None
            or parsed.fragment
        ):
            raise JobValidationError("target URL is invalid")
        scheme = parsed.scheme.casefold()
        expected_port = 443 if scheme == "https" else 80
        if port is not None and port != expected_port:
            raise JobValidationError("target URL uses a disallowed port")
        try:
            hostname = parsed.hostname.rstrip(".").encode("idna").decode("ascii").casefold()
        except UnicodeError as error:
            raise JobValidationError("target URL is invalid") from error
        if not hostname or len(hostname) > 253:
            raise JobValidationError("target URL is invalid")
        try:
            literal = ipaddress.ip_address(hostname)
            addresses = (str(literal),)
        except ValueError:
            try:
                addresses = tuple(dict.fromkeys(self.resolver(hostname, expected_port)))
            except OSError as error:
                raise JobValidationError("target host could not be resolved") from error
        if not addresses:
            raise JobValidationError("target host could not be resolved")
        normalized: list[str] = []
        for value in addresses:
            try:
                address = ipaddress.ip_address(value)
            except ValueError as error:
                raise JobValidationError("target host returned an invalid address") from error
            if not address.is_global:
                raise JobValidationError("target host is not publicly routable")
            normalized.append(str(address))
        host = f"[{hostname}]" if ":" in hostname else hostname
        path = parsed.path or "/"
        canonical = urlunsplit(SplitResult(scheme, host, path, parsed.query, ""))
        return ResolvedTarget(canonical, hostname, expected_port, tuple(sorted(set(normalized))))
