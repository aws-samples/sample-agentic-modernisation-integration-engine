"""``repo_id`` validation shared by everything that turns one into a filesystem path.

``repo_id`` arrives from the URL on every ``/{repo_id}``-shaped route and is used to
build a path under ``settings.storage_path``. The rule lives here, in one module, so
the record store and the download service cannot drift apart on what counts as a safe
identifier — two regexes would be two chances to get it wrong.
"""

import re

#: ``repo_id`` is a 12-char uuid prefix in production; tests use short slugs. Anything
#: with a separator, dot segment or unusual character is rejected outright, so a
#: traversal attempt cannot reach the filesystem at all.
REPO_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$")


class InvalidRepoIdError(ValueError):
    """The supplied repo_id is not a well-formed identifier."""


def is_valid_repo_id(repo_id: str) -> bool:
    """True if ``repo_id`` is a well-formed identifier."""
    return bool(REPO_ID_PATTERN.match(repo_id or ""))


def validate_repo_id(repo_id: str) -> str:
    """Reject any repo_id that could escape the storage root.

    Raises:
        InvalidRepoIdError: If the identifier is malformed.
    """
    if not is_valid_repo_id(repo_id):
        raise InvalidRepoIdError(f"Invalid repo_id: {repo_id!r}")
    return repo_id
