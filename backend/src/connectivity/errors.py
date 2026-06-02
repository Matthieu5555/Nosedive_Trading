"""Errors raised by the connectivity layer.

Each carries the value that triggered it, so a failure says exactly what broke
rather than just "connection error". Disconnects and connect failures are expected
operational variants the supervisor recovers from; the client-id errors are caller
bugs surfaced loudly rather than papered over with a silent default.
"""

from __future__ import annotations


class ConnectivityError(Exception):
    """Base class for all connectivity-layer failures."""


class SessionDisconnected(ConnectivityError):
    """The broker session dropped mid-use.

    Raised by a session when a stream or request finds the connection gone. The
    :class:`~connectivity.supervisor.SessionSupervisor` catches it, reconnects on the
    backoff schedule, re-subscribes, and resumes — recording the outage as a gap.
    """

    def __init__(self, reason: str) -> None:
        self.reason = reason
        super().__init__(f"broker session disconnected: {reason}")


class ConnectionFailed(ConnectivityError):
    """A single connect attempt failed.

    Distinct from :class:`SessionDisconnected`: this is a failure to establish the
    session in the first place, which the supervisor retries on the backoff schedule.
    """

    def __init__(self, reason: str) -> None:
        self.reason = reason
        super().__init__(f"broker connect attempt failed: {reason}")


class UnknownServiceError(ConnectivityError):
    """A client id was requested for a service with no reserved id band.

    The client-id convention only knows the services it has bands for; an unknown
    name is a caller bug (a typo, or a new service that needs a band), surfaced with
    the list of known services rather than silently handed a colliding id.
    """

    def __init__(self, service: str, known: tuple[str, ...]) -> None:
        self.service = service
        self.known = known
        super().__init__(
            f"no client-id band reserved for service {service!r}; known services: {known!r}"
        )


class ClientIdError(ConnectivityError):
    """A service instance index fell outside its reserved client-id band.

    Bands are spaced so instances never bleed into the next service's range; an index
    at or beyond the band width would collide, so it is refused with diagnostics.
    """

    def __init__(self, service: str, instance: int, band_width: int) -> None:
        self.service = service
        self.instance = instance
        self.band_width = band_width
        super().__init__(
            f"instance {instance} for service {service!r} is outside its band "
            f"(width {band_width}); it would collide with another service's ids"
        )
