"""
Generates MiniVAN list numbers for every list on the Packet Tracker, through My Turfs.

    python -m flows.packetland.van_list_numbers.van_list_numbers --stored-login            # rehearsal, nothing generated
    python -m flows.packetland.van_list_numbers.van_list_numbers --stored-login --commit   # generates them all

It reads the same rows the print does (whatever the Queue picked), logs in once, and for
each list: searches its name in My Turfs' Name or List # and clicks Refresh, ticks the box
next to 'Turf', chooses Generate List Number from Quick Actions, checks the pop-up counts as
many map regions as the Packet Tracker has under that name, clicks Next, and fills each map
region's page from the sheet. With --commit it clicks Generate List Numbers/Next on each
page; without it, it fills the first page in and clicks Exit.

The steps themselves are the ones in capture_my_turfs.py. A list that fails is saved (page,
screenshot) and skipped, and the run carries on with the next one; the summary names them.
List numbers are not printed: this is the no-PDF route. For paper packets, run the print.
"""

import argparse
import csv
import json
import os
import re
import shlex
import time

from datetime import datetime, timedelta

from core_utilities.van_browser_utilities import (
    DEFAULT_ACTIONID_USER,
    MY_VOTERS,
    resolve_actionid_credentials,
)
from flows.packetland.mapped_packets import TEST_STATE, finish, load_mapped_packets, row_context
from flows.packetland.van_list_numbers import capture_my_turfs as turfs
from flows.van_automation.session import add_login_arguments, check_login_arguments, progress
from flows.van_automation.target_slots import by_state, committee_for
from flows.van_automation.van_ui import ARTIFACT_DIR, VanBrowser

### -------------------------------------------------------------------------------------------------------------------------------------------- ###
## VAN LIST NUMBERS FLOW ------------------------------------------------------------------------------------------------------------------------- ###
### -------------------------------------------------------------------------------------------------------------------------------------------- ###

# Where My Turfs lives, once the first list has found it through the home page's link.
MY_TURFS_URL = "[VAN URL]"

# Every list a --commit run generates is written here the moment it is done, so a run that
# is stopped or crashes can simply be started again: lists generated in the last
# DONE_WINDOW_HOURS are skipped (--again includes them). Next weekend's run starts clean.
DONE_LOG = os.path.join(ARTIFACT_DIR, "list_numbers_done.csv")
DONE_FIELDS = ["generated_at", "map_region_name", "map_region_ids", "pages"]
DONE_WINDOW_HOURS = 24

# What a failure says when the browser is not on My Turfs at all: logged out, or an error page.
PAGE_LOST_SIGNS = ["there is no Edit next to Filters", "the filters have no 'Name or List #' box",
                   "there is no My Turfs link"]

# After this many lists fail one after another, even with the session recovered, the run stops.
MAX_FAILURES_IN_A_ROW = 8

### -------------------------------------------------------------------------------------------------------------------------------------------- ###

def record_done(report: dict):
    """Adds a generated list to DONE_LOG. Never raises: the log must not stop the run."""
    try:
        os.makedirs(ARTIFACT_DIR, exist_ok=True)
        new = not os.path.exists(DONE_LOG)
        with open(DONE_LOG, "a", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=DONE_FIELDS)
            if new:
                writer.writeheader()
            writer.writerow({
                "generated_at": datetime.now().isoformat(timespec="seconds"),
                "map_region_name": report["map_region_name"],
                "map_region_ids": ";".join(str(i) for i in report["map_region_ids"]),
                "pages": ", ".join(report["generated"]),
            })
    except Exception as error:
        print(f"    (could not note this list in {DONE_LOG}: {error})", flush=True)


def recently_done(hours: float = DONE_WINDOW_HOURS) -> set:
    """The lists DONE_LOG shows as generated in the last so many hours."""
    if not os.path.exists(DONE_LOG):
        return set()
    since = datetime.now() - timedelta(hours=hours)
    names = set()
    with open(DONE_LOG, newline="", encoding="utf-8") as handle:
        for row in csv.DictReader(handle):
            try:
                if datetime.fromisoformat(row["generated_at"]) >= since:
                    names.add(row["map_region_name"])
            except (KeyError, ValueError):
                continue
    return names

### -------------------------------------------------------------------------------------------------------------------------------------------- ###

def failed_names(path: str) -> list:
    """
    The lists that failed in an earlier run, for --retry. Takes either the run's summary
    (van_automation_artifacts/<time>_list_numbers_summary.txt) or the failed_lists.txt the
    run left in its folder.
    """
    with open(path, encoding="utf-8") as handle:
        text = handle.read()
    try:
        failures = json.loads(text).get("failures", [])
        # A failure is named 'staging location / folder / map region'.
        names = [str(failure.get("context", "")).split(" / ")[-1].strip() for failure in failures]
    except (json.JSONDecodeError, AttributeError):
        names = [line.strip() for line in text.splitlines()]
    names = list(dict.fromkeys(name for name in names if name))
    if not names:
        raise SystemExit(f"{path} names no failed lists to retry.")
    return names

### -------------------------------------------------------------------------------------------------------------------------------------------- ###

def generated_names(path: str) -> set:
    """
    The lists an earlier run generated, for --skip. Takes the terminal output of that run
    saved as text (its 'generated: NAME (map region ...)' lines), or its summary file.
    """
    with open(path, encoding="utf-8", errors="replace") as handle:
        text = handle.read()
    names = set(re.findall(r"generated: (.+?) \(map region", text))
    try:
        names |= {report.get("map_region_name") for report in json.loads(text).get("reports", [])
                  if report.get("action") == "generated" and report.get("map_region_name")}
    except (json.JSONDecodeError, AttributeError):
        pass
    if not names:
        raise SystemExit(f"{path} shows no lists as generated, so there is nothing to skip.")
    return names

### -------------------------------------------------------------------------------------------------------------------------------------------- ###

def by_name(rows: list) -> list:
    """
    The rows as lists, one per map region name, in the sheet's order. VAN lets regions
    share a name and a search for it shows them all, so they are done together.
    """
    lists = {}
    for row in rows:
        lists.setdefault(row["map_region_name"], []).append(row)
    return list(lists.values())

### -------------------------------------------------------------------------------------------------------------------------------------------- ###

def leave_generate_page(browser):
    """After a failure, clicks Exit if the run is still on a Generate List Numbers page."""
    try:
        if browser.driver.execute_script(turfs.REGION_HEADING_SCRIPT):
            turfs.click_exit(browser, {})
    except Exception:
        pass

### -------------------------------------------------------------------------------------------------------------------------------------------- ###

def process_list(browser, rows: list, commit: bool, first: bool, again: bool = False) -> dict:
    """Runs the My Turfs steps for one list and returns its report. Raises what stopped it."""
    name = rows[0]["map_region_name"]
    notes = {"name": name, "rows": rows, "commit": commit, "save": lambda label: None}
    report = {"context": row_context(rows[0]), "map_region_name": name,
              "map_region_ids": [row["map_region_id"] for row in rows],
              "generated": [], "pages": []}

    if first:
        turfs.open_my_turfs(browser, notes)
    else:
        browser.driver.get(MY_TURFS_URL)
        turfs.settle(browser)

    turfs.search_for_list(browser, notes)

    # A list whose turfs all show a list number already (from an earlier run, or a print) is
    # left alone, so a rerun does not give it a second one. --again generates it anyway.
    if not again and turfs.already_numbered(turfs.grid_rows(browser.driver)):
        report["action"] = "already numbered"
        return report

    report["ticked"] = turfs.select_turfs(browser, notes)
    report["popup"] = turfs.choose_generate(browser, notes)
    turfs.click_next_in_popup(browser, notes)

    try:
        if commit:
            turfs.generate_each_region(browser, notes)
            report["action"] = "generated"
        else:
            turfs.fill_generate_page(browser, notes)
            turfs.click_exit(browser, notes)
            report["action"] = "dry run"
    finally:
        # Kept on a failure too, so a list that stopped halfway says which pages went through.
        report["pages"] = notes.get("pages", [])
        report["generated"] = notes.get("generated", [])

    return report

### -------------------------------------------------------------------------------------------------------------------------------------------- ###

def page_lost(error: Exception) -> bool:
    """
    Whether a failure means the browser is not on My Turfs at all (logged out, an error
    page), rather than something about this list. These happen before anything is ticked,
    so the list is safe to try again once the session is back.
    """
    return any(sign in str(error) for sign in PAGE_LOST_SIGNS)


def recover(browser, state: str, login) -> str:
    """
    Gets the session back onto My Turfs: reloads it, and if the filters are still not there,
    starts a fresh login and switches back into the state. Returns what it took.
    """
    driver = browser.driver
    try:
        turfs.answer_alert(driver, accept=False)
        driver.get(MY_TURFS_URL)
        turfs.settle(browser)
        if (driver.execute_script(turfs.FILTER_EDIT_SCRIPT)
                or driver.execute_script(turfs.INPUT_BY_LABEL_SCRIPT, turfs.NAME_FILTER_LABEL)):
            return "reloaded My Turfs"
    except Exception:
        pass

    # A fresh login: forget the old session first, or the login page may not show.
    driver.delete_all_cookies()
    login()
    browser.switch_session(state, committee_for(state), database=MY_VOTERS)
    return "logged in again"


def process_state(browser, state: str, rows: list, commit: bool, folder: str,
                  again: bool = False, login=None) -> tuple:
    """
    Generates every list in one state and returns (reports, failures).

    When VAN stops showing My Turfs (the session lapsed, an error page), the session is
    recovered with recover() and the list tried once more. If lists still fail one after
    another, the run stops rather than failing the rest of the Queue in seconds each; the
    lists it did not get to are named as failed, so --retry picks them up.
    """
    reports, failures = [], []
    lists = by_name(rows)

    try:
        browser.switch_session(state, committee_for(state), database=MY_VOTERS)
    except Exception as session_error:
        print(f"Failed: {session_error}", flush=True)
        return reports, [{"stage": "Session", "context": row_context(row),
                          "error": f"Could not switch the session into {state}: {session_error}"}
                         for row in rows]

    in_a_row = 0
    fresh = True   # next list opens My Turfs from the home page's link
    for index, same in enumerate(lists, start=1):
        name = same[0]["map_region_name"]
        count = f" ({len(same)} map regions)" if len(same) > 1 else ""
        progress(index, len(lists), f"{name}{count}")
        try:
            try:
                report = process_list(browser, same, commit, first=fresh, again=again)
            except Exception as first_error:
                if not (login and page_lost(first_error)):
                    raise
                print(f"    VAN is not showing My Turfs ({first_error}).", flush=True)
                how = recover(browser, state, login)
                print(f"    Recovered: {how}. Trying {name} again.", flush=True)
                report = process_list(browser, same, commit, first=how == "logged in again",
                                      again=again)
            fresh = False
            in_a_row = 0
            reports.append(report)
            if report["action"] == "already numbered":
                print(f"skipped: {name} (its turfs already have list numbers)", flush=True)
                continue
            pages = ", ".join(report["generated"]) if commit else "first page filled, then Exit"
            print(f"{report['action']}: {name} (map region {pages})" if commit
                  else f"{report['action']}: {name} ({pages})", flush=True)
            if commit and report["generated"]:
                record_done(report)
        except Exception as list_error:
            fresh = False
            in_a_row += 1
            reason = str(list_error).strip().splitlines()[0] if str(list_error).strip() \
                else type(list_error).__name__
            try:
                left_open = turfs.answer_alert(browser.driver, accept=False)
            except Exception:
                left_open = ""
            if left_open:
                reason += f" (VAN's alert said {left_open!r})"
            print(f"Failed: {reason}", flush=True)
            # Whatever goes wrong saving the page, the run carries on to the next list.
            try:
                saved = turfs.save_screen(browser.driver, folder, index, f"{name}_failed")
            except Exception:
                saved = []
            failures.append({"stage": "List number", "context": row_context(same[0]),
                             "error": reason, "artifacts": saved[:3]})
            leave_generate_page(browser)

            if in_a_row >= MAX_FAILURES_IN_A_ROW:
                rest = lists[index:]
                print(f"\nStopping: {in_a_row} lists in a row failed, so something is wrong with "
                      f"the session rather than the lists. The {len(rest)} lists not tried yet "
                      "are counted as failed, so --retry will pick them up.", flush=True)
                failures += [{"stage": "List number", "context": row_context(other[0]),
                              "error": f"not tried: the run stopped after {in_a_row} failures in a row"}
                             for other in rest]
                break

    return reports, failures

### -------------------------------------------------------------------------------------------------------------------------------------------- ###

def generate_list_numbers(commit: bool = False, headless: bool = False, stored_login: bool = True,
                          only: list = None, region: list = None, test_district: bool = False,
                          skip: set = None, again: bool = False) -> list:
    """Generates list numbers for every list on the Packet Tracker, less any in skip."""
    rows = load_mapped_packets(only, region, test_district)
    names_before = {row["map_region_name"] for row in rows}

    if skip:
        rows = [row for row in rows if row["map_region_name"] not in skip]
        dropped = names_before - {row["map_region_name"] for row in rows}
        print(f"Skipping {len(dropped)} lists already generated in the earlier run.", flush=True)

    if not again:
        done = recently_done()
        dropped = {row["map_region_name"] for row in rows} & done
        if dropped:
            rows = [row for row in rows if row["map_region_name"] not in done]
            print(f"Skipping {len(dropped)} lists generated in the last {DONE_WINDOW_HOURS} hours "
                  f"({DONE_LOG}). Add --again to include them.", flush=True)

    if not rows:
        print("Nothing left to do: every list on the Packet Tracker was already generated.")
        return []
    lists = by_name(rows)
    print(f"\n[{'COMMIT' if commit else 'DRY RUN'}] list numbers for {len(lists)} lists "
          f"({len(rows)} map regions) across {len(by_state(rows))} states."
          + ("" if commit else " Nothing is generated without --commit."), flush=True)

    folder = os.path.join(ARTIFACT_DIR, f"list_numbers_{time.strftime('%Y%m%d_%H%M%S')}")
    os.makedirs(folder, exist_ok=True)

    reports, failures = [], []
    driver = turfs.quiet_driver(headless)
    browser = VanBrowser(driver)

    def login():
        """Logs in, at the start and again whenever the session has to be recovered."""
        if stored_login:
            email, password, totp_secret = resolve_actionid_credentials(DEFAULT_ACTIONID_USER)
            print(f"Logging in as {email}", flush=True)
            browser.login(email, password, totp_secret)
        else:
            browser.wait_for_manual_login()

    try:
        login()

        # VAN can take the ActionID sign-in and still turn the account away, on a page of its
        # own. Nothing can be done in the session then, so say so plainly and stop.
        if "mode=notconnected" in driver.current_url.lower():
            saved = turfs.save_screen(driver, folder, 0, "login_not_connected")
            print("\nVAN turned the login away. ActionID signed in as the stored account, but "
                  "VoteBuilder answered with its 'not connected' page "
                  f"({driver.current_url}).\nThat is VAN refusing the account, not this script: "
                  "log in to votebuilder.com yourself to see what it wants, or ask your VAN admin "
                  "to check the account's access.\nNothing was generated."
                  + (f"\nWhat VAN showed: {os.path.abspath(saved[0])}" if saved else ""), flush=True)
            return []

        for state, in_state in by_state(rows).items():
            print(f"\n=== {state} ===", flush=True)
            state_reports, state_failures = process_state(browser, state, in_state, commit,
                                                          folder, again, login)
            reports += state_reports
            failures += state_failures
    finally:
        driver.quit()

    skipped = [report for report in reports if report["action"] == "already numbered"]
    worked = [report for report in reports if report["action"] != "already numbered"]
    if skipped:
        print(f"\nSKIPPED {len(skipped)} lists whose turfs already have list numbers "
              "(add --again to generate them anyway):", flush=True)
        for report in skipped:
            print(f"  {report['map_region_name']}", flush=True)
    if commit:
        made = sum(len(report["generated"]) for report in worked)
        print(f"\nGENERATED list numbers for {made} map regions in {len(worked)} lists.", flush=True)
    else:
        print(f"\nDRY RUN: {len(worked)} lists filled in and exited. NOTHING WAS GENERATED; "
              "add --commit to generate.", flush=True)
    if failures:
        print(f"Pages from the {len(failures)} failed lists are in {os.path.abspath(folder)}", flush=True)
        retry_file = os.path.join(folder, "failed_lists.txt")
        with open(retry_file, "w", encoding="utf-8") as handle:
            handle.write("\n".join(dict.fromkeys(
                failure["context"].split(" / ")[-1] for failure in failures)) + "\n")
        print("\nTo retry just the failed lists:\n    python3 -m flows.packetland.van_list_numbers."
              f"van_list_numbers --stored-login{' --commit' if commit else ''} --retry "
              f"{shlex.quote(retry_file)}", flush=True)

    return finish("list numbers", reports, failures)

### -------------------------------------------------------------------------------------------------------------------------------------------- ###

def main():
    parser = argparse.ArgumentParser(
        description="Generate MiniVAN list numbers for the lists on the Packet Tracker, through My Turfs"
    )
    add_login_arguments(parser)
    parser.add_argument("--commit", action="store_true",
                        help="Actually generate. Without it, each list's page is filled in and exited.")
    parser.add_argument("--only", nargs="+", metavar="CD",
                        help="Only these staging locations (the Packet Tracker's cd column).")
    parser.add_argument("--region", nargs="+", metavar="NAME",
                        help="Only these lists, by map region name.")
    parser.add_argument("--test-district", action="store_true",
                        help=f"Use the {TEST_STATE} test region instead of reading the sheet.")
    parser.add_argument("--retry", metavar="FILE",
                        help="Only the lists that failed in an earlier run: its summary file "
                             "(van_automation_artifacts/<time>_list_numbers_summary.txt) or the "
                             "failed_lists.txt in its folder.")
    parser.add_argument("--skip", metavar="FILE",
                        help="Leave out the lists an earlier run generated: that run's terminal "
                             "output saved as a text file, or its summary file.")
    parser.add_argument("--again", action="store_true",
                        help="Generate even for lists already done: ones generated in the last "
                             f"{DONE_WINDOW_HOURS} hours, or whose turfs show a list number in "
                             "My Turfs. Without it those lists are skipped.")
    args = parser.parse_args()
    check_login_arguments(parser, args)

    region = args.region
    if args.retry:
        region = failed_names(args.retry)
        print(f"Retrying {len(region)} lists that failed in {args.retry}.", flush=True)
    skip = generated_names(args.skip) if args.skip else None

    generate_list_numbers(args.commit, args.headless, args.stored_login, args.only,
                          region, args.test_district, skip, args.again)


if __name__ == "__main__":
    main()