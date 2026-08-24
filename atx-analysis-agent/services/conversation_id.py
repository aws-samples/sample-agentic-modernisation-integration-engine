"""``conversation_id`` validation shared by everything that turns one into a path.

``conversation_id`` arrives from the URL on every ``/conversations/{id}/*`` route and
from the request body of ``POST /analyze``, and is used to build a path under
``settings.storage_path``. The rule lives here, in one module, so the record store, the
command service and the file browser cannot drift apart on what counts as a safe
identifier — three ad-hoc checks were three chances to get it wrong, and two of them
were ``str(resolved).startswith(str(root))``, which accepts a sibling directory whose
name merely shares the root's prefix.

Mirrors ``atx-transform-agent/services/repo_id.py``; the two ATX agents must not
diverge on how durable state works, and that includes what may name a unit of it.
"""

import re

#: ``conversation_id`` is ``atx_<YYYYmmdd>_<HHMMSS>_<8 hex>`` in production, and clients
#: may supply their own via ``POST /analyze``. Anything with a separator, dot segment or
#: unusual character is rejected outright, so a traversal attempt never reaches the
#: filesystem at all.
CONVERSATION_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$")


class InvalidConversationIdError(ValueError):
    """The supplied conversation_id is not a well-formed identifier."""


def is_valid_conversation_id(conversation_id: str) -> bool:
    """True if ``conversation_id`` is a well-formed identifier."""
    return bool(CONVERSATION_ID_PATTERN.match(conversation_id or ""))


def validate_conversation_id(conversation_id: str) -> str:
    """Reject any conversation_id that could escape the storage root.

    Raises:
        InvalidConversationIdError: If the identifier is malformed.
    """
    if not is_valid_conversation_id(conversation_id):
        raise InvalidConversationIdError(f"Invalid conversation_id: {conversation_id!r}")
    return conversation_id
