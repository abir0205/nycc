"""
The two helpers the packet flows share for walking VAN a state at a time.

These flows work in MyVoters as one committee in one state, so the session is switched
once per run.
"""

# Your committee exactly as VoteBuilder's committee switcher shows it. Set it to None
# when the bot account has only one committee: VAN shows no dropdown then, and asking
# for a committee fails the switch. Left as a name VAN does not have, the first run
# fails with the list of committees the account does have.
COMMITTEE = None


def committee_for(state: str) -> str | None:
    """The committee to switch into, or None where VAN offers no picker."""
    return COMMITTEE


def by_state(slots: list) -> dict:
    """
    Groups the rows by state, since the session switches state and not committee.

    Switching costs two postbacks through the picker, so a run does one switch per
    state rather than one per district.
    """
    grouped = {}
    for slot in slots:
        grouped.setdefault(slot["state"], []).append(slot)
    return grouped
