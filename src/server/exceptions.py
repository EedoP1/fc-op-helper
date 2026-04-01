"""Application-specific exceptions for the server."""


class InsufficientDataError(Exception):
    """Raised when scoring has insufficient observations for a player."""
    pass


class ScoringError(Exception):
    """Raised when the scoring pipeline encounters a fatal error."""
    pass
