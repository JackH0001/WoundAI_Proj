"""Storage exceptions shared without importing a medical storage implementation."""


class ImmutableConflict(ValueError):
    """The key exists with different content; callers must not overwrite it."""
