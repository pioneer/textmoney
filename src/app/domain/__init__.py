"""Pure application rules, independent of the presentation framework."""


def readiness_message(*, ready: bool) -> str:
    return "Ready" if ready else "Not ready"
