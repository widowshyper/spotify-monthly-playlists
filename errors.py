"""Exception types with messages suitable for showing directly to the user."""


class SpotifyAppError(Exception):
    """Base class for all errors the UI should display."""


class ConfigError(SpotifyAppError):
    """Missing or invalid local configuration (e.g. no Client ID)."""


class AuthError(SpotifyAppError):
    """Login failed, or the cached session expired and needs a fresh login."""


class NotLoggedInError(AuthError):
    pass


class PremiumRequiredError(SpotifyAppError):
    pass


class NetworkError(SpotifyAppError):
    pass


class ApiError(SpotifyAppError):
    def __init__(self, message: str, status: int | None = None):
        super().__init__(message)
        self.status = status
