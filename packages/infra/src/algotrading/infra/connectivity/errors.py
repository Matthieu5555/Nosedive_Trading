from __future__ import annotations


class ConnectivityError(Exception):
    pass


class TransportError(ConnectivityError):
    pass


class SessionDisconnected(ConnectivityError):

    def __init__(self, reason: str) -> None:
        self.reason = reason
        super().__init__(f"broker session disconnected: {reason}")


class ConnectionFailed(ConnectivityError):

    def __init__(self, reason: str) -> None:
        self.reason = reason
        super().__init__(f"broker connect attempt failed: {reason}")


class UnknownServiceError(ConnectivityError):

    def __init__(self, service: str, known: tuple[str, ...]) -> None:
        self.service = service
        self.known = known
        super().__init__(
            f"no client-id band reserved for service {service!r}; known services: {known!r}"
        )


class ClientIdError(ConnectivityError):

    def __init__(self, service: str, instance: int, band_width: int) -> None:
        self.service = service
        self.instance = instance
        self.band_width = band_width
        super().__init__(
            f"instance {instance} for service {service!r} is outside its band "
            f"(width {band_width}); it would collide with another service's ids"
        )
