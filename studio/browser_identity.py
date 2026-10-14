"""Who the connector extension is, and which versions this server will talk to."""
import re

EXTENSION_ID = "dejicdalmlgoilfjfggbebfimfojlflf"
EXTENSION_ORIGIN = "chrome-extension://" + EXTENSION_ID

# The oldest extension whose read protocol this server speaks. Compare whole
# versions as numbers, never as text: the gate used to match the literal "1.3."
# and then compare patch numbers, so the day the extension reported 1.4.0 the
# patch part read as 0 -- below every minimum -- and every browser read would
# have gone offline silently, with nothing in the logs but "browser_offline".
BASELINE = (1, 3, 0)


def extension_version(text):
    """Parse a reported version into comparable numbers; None if it is not one.

    '1.4.0' -> (1, 4, 0). Anything with a suffix ('1.4.0-beta') is not a version
    we shipped, so it is refused rather than guessed at.
    """
    match = re.fullmatch(r"\s*(\d+(?:\.\d+)*)\s*", text) if isinstance(text, str) else None
    return tuple(int(part) for part in match.group(1).split(".")) if match else None


def extension_at_least(text, minimum):
    """Is a reported version at or above `minimum`?

    A shorter report is padded with zeros, so an extension that says "1.4"
    satisfies (1, 3, 7). A later major satisfies an earlier minimum: the server
    keeps every old route, so forward compatibility is the safe direction.
    """
    version = extension_version(text)
    if version is None:
        return False
    minimum = tuple(minimum)
    return version + (0,) * (len(minimum) - len(version)) >= minimum
