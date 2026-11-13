"""The one exception these flows raise, in its own file so both van_ui and
controls can import it without importing each other."""


class VanUiError(Exception):
    """Raised when the page is not in the state the flow expects."""
