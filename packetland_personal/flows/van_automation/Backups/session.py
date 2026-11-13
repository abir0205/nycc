"""
The plumbing every VoteBuilder flow needs: a logged-in session, the flags that
get one, and how a long run reports itself.

"""

import json
import os
import re
import sys
import time
import traceback

from core_utilities.van_browser_utilities import (
    DEFAULT_ACTIONID_USER,
    create_driver,
    resolve_actionid_credentials,
)
from flows.van_automation.van_ui import ARTIFACT_DIR, VanBrowser

# The committee tracker sheet: the districts we sync to VAN at all.
COMMITTEE_TRACKER = (
    "<GCP_PROJECT>.<DATASET>.<COMMITTEE_TRACKER_TABLE>"
)

# Where a test run goes instead of the live thing. Hawaii has no target
# district, so nothing in it is a campaign's working committee or a live
# universe, and it is deliberately absent from both trackers.
TEST_STATE = "HI"

# The state prefix every district CD carries. A row that is not a district
# (Distributed) has no state to switch the session to, so it is skipped rather
# than failing mid-run.
CD_PATTERN = re.compile(r"^([A-Z]{2})-")


def open_session(headless: bool, stored_login: bool,
                 user: str = DEFAULT_ACTIONID_USER) -> tuple:
    """
    Opens Chrome and gets through login. Returns the driver and the browser.

    user picks which stored account to log in as, and only matters with
    stored_login.
    """
    driver = create_driver(headless)
    browser = VanBrowser(driver)
    if stored_login:
        email, password, totp_secret = resolve_actionid_credentials(user)
        print(f"Logging in as {email}")
        browser.login(email, password, totp_secret)
    else:
        browser.wait_for_manual_login()
    return driver, browser


def progress(index: int, total: int, label: str):
    """
    Marks the start of one item in a long run.

    Flushed, because print to a pipe is block buffered and these runs are
    normally watched through tee.
    """
    print(f"\n--- [{index}/{total}] {label} ---", flush=True)


def pause_for_review(label: str) -> bool:
    """
    Holds the run on what is on screen until somebody has looked at it.

    Without this a dry run is only readable after the fact, in the summary, since
    the next item loads a page over the one somebody wanted to check. Returns
    whether it actually waited, so a non-interactive run says so rather than
    looking like it paused.
    """
    if not sys.stdin.isatty():
        print(f"Nothing to read stdin from, so not pausing on {label}.", flush=True)
        return False

    print(f"\nPaused on {label}. Look it over in the browser, then press Enter "
          "to carry on (Ctrl-C to stop here).", flush=True)
    input()
    return True


def write_summary(what: str, reports: list, failures: list) -> str:
    """
    Files the full summary next to the artifacts and returns where it went.

    A sweep of 50 districts is four slots each, and every failure carries a
    chromedriver stack, so the summary runs to thousands of lines and scrolls the
    run that produced it out of the terminal. The whole thing goes in the file;
    what prints is the screenful in report_summary below.

    Same directory as the screenshots and page sources, and gitignored with them,
    since it is the same kind of thing: evidence of a run nobody watched.
    """
    os.makedirs(ARTIFACT_DIR, exist_ok=True)
    stem = re.sub(r"[^A-Za-z0-9]+", "_", what).strip("_")
    path = os.path.join(
        ARTIFACT_DIR, f"{time.strftime('%Y%m%d_%H%M%S')}_{stem}_summary.txt"
    )

    with open(path, "w") as handle:
        json.dump({"reports": reports, "failures": failures}, handle, indent=2)
        handle.write("\n")

    return path


def report_summary(what: str, reports: list, failures: list) -> str:
    """
    Prints what is worth reading on the way past, and files the rest.

    One line per failure, cut to the first line of its message: Selenium puts a
    twenty frame chromedriver stack in every one of those, and it says nothing the
    frame in "where" does not say better.
    """
    path = write_summary(what, reports, failures)

    print("\n=== SUMMARY ===")
    print(f"{len(reports)} {what} built, {len(failures)} failed. "
          f"Full summary: {path}")

    if failures:
        print("\nFailed:")
        for failure in failures:
            slot = failure.get("slot") or f"{failure.get('state', '?')}, whole state"
            where = failure.get("where")
            message = str(failure.get("error", "")).strip().splitlines()
            print(f"  {slot}: {where + ': ' if where else ''}"
                  f"{message[0] if message else 'no message'}")

    return path


def where_it_failed(error: Exception) -> str:
    """
    The line in our own code a failure came from, as file:line in function.

    Selenium's own message is often the least useful part of it: a stale element
    reference names neither the element nor the lookup, and there are a dozen of
    those in a slot. These runs are long and unattended and VAN's state is gone by
    the time anybody reads the summary, so the frame is worth keeping next to the
    message rather than left in a traceback nobody sees.

    Our own frames only, innermost last. Selenium's internals are the same four
    lines whatever went wrong.
    """
    frames = [
        frame for frame in traceback.extract_tb(error.__traceback__)
        if "van_automation" in frame.filename or "core_utilities" in frame.filename
    ]
    if not frames:
        return ""

    return " -> ".join(
        f"{frame.filename.rsplit('/', 1)[-1]}:{frame.lineno} in {frame.name}"
        for frame in frames[-3:]
    )


def add_login_arguments(parser):
    """The flags anything needing a VoteBuilder session takes."""
    parser.add_argument("--headless", action="store_true",
                        help="Run Chrome headless. Needs --stored-login.")
    parser.add_argument("--stored-login", action="store_true",
                        help="Log in with the stored ActionID credentials and TOTP seed.")
    return parser


def check_login_arguments(parser, args):
    """Rejects flag combinations that cannot work, before Chrome opens."""
    if args.headless and not args.stored_login:
        parser.error("--headless needs --stored-login, otherwise there is no way to log in.")
