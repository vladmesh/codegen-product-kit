"""Package-local route response used through an absolute intra-package import."""


def response() -> dict[str, bool]:
    """Return the synthetic package status payload."""

    return {"synthetic": True}
