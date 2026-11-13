"""
Prints the turf packets organizers asked for on the Packet Tracker sheet.

There is no print API, so this drives the wizard in a browser, one region at a time, as
the national committee in each state. It runs after van_refresh_packets in the chain.
Printing is what puts a packet in MiniVAN. The PDFs are downloaded by van_download_packets.
"""

import argparse

from core_utilities.van_browser_utilities import MY_VOTERS
from flows.packetland.mapped_packets import (
    TEST_STATE,
    finish,
    load_mapped_packets,
    row_context,
)
from flows.van_automation.constants import CONTACTED_HOW
from flows.van_automation.session import (
    add_login_arguments,
    check_login_arguments,
    open_session,
    pause_for_review,
    progress,
    where_it_failed,
)
from flows.van_automation.target_slots import by_state, committee_for

### -------------------------------------------------------------------------------------------------------------------------------------------- ###
## VAN PRINT PACKETS FLOW ----------------------------------------------------------------------------------------------------------------------- ###
### -------------------------------------------------------------------------------------------------------------------------------------------- ###

### -------------------------------------------------------------------------------------------------------------------------------------------- ###

# The most characters VAN's PDF title box takes (its maxlength).
TITLE_LIMIT = 50


def pdf_title(row: dict) -> str:
    """
    The title this gives a region's PDF: 'SL-R06-Mount-Pleasant_Mtpl-19 (1028077)'.

    The PDF Print Jobs list shows titles and nothing else, and VAN lets two regions share
    a name, so the region id goes on the end. van_download_packets matches on this title
    and names the Drive file with it.

    A title over TITLE_LIMIT is shortened, since VAN's box will not take it: the space
    goes first ('SL-R05-Washington/Millbrook_Union-Vale-02(1033759)'), then the end of
    the name if that is still not enough.
    """
    name, suffix = row["map_region_name"], f"({row['map_region_id']})"
    title = f"{name} {suffix}"
    if len(title) <= TITLE_LIMIT:
        return title

    # Too long for VAN's title box. Dropping the space is enough for most; past that the
    # name is cut short. The id stays whole, since it is what keeps the title unique, and
    # the download uses this same function, so it still finds the PDF.
    return f"{name[:TITLE_LIMIT - len(suffix)]}{suffix}"

### -------------------------------------------------------------------------------------------------------------------------------------------- ###

def wizard_choices(row: dict) -> dict:
    """What the four print wizard dropdowns should be set to for this row."""

    return {
        "report_format": row.get("report_format"),
        "script": row.get("script"),
        "contacted_how": CONTACTED_HOW,
        "minivan_campaign": row.get("minivan_campaign"),
    }

### -------------------------------------------------------------------------------------------------------------------------------------------- ###

def missing_wizard_values(choices: dict, offered: dict) -> list:
    """
    Returns a message per sheet value the wizard does not offer, naming what it does.
    A blank campaign is fine; a blank anything else is caught by the mart and this backstop.
    """
    problems = []

    for field, wanted in choices.items():
        if not wanted:
            if field != "minivan_campaign":
                problems.append(f"{field} is blank on the sheet.")
            continue

        available = offered.get(field) or {}
        if wanted not in available:
            problems.append(
                f"{field} {wanted!r} is not offered by the wizard. It has: "
                f"{', '.join(sorted(available)) or 'nothing'}"
            )

    return problems

### -------------------------------------------------------------------------------------------------------------------------------------------- ###

def check_campaign_did_not_override(applied: dict, choices: dict) -> list:
    """
    Returns a message per field that landed differently from the sheet. Picking a campaign
    disables Script and Contacted How and sets them itself, so what is on the page after
    is what prints, whatever was asked for.
    """
    problems = []

    for field in ["script", "contacted_how"]:
        wanted = choices.get(field)
        landed = applied.get(field) or ""
        if wanted and landed != wanted:
            problems.append(
                f"MiniVAN campaign {choices['minivan_campaign']!r} left {field} as "
                f"{landed or 'blank'!r}, but the sheet asks for {wanted!r}."
            )

    return problems

### -------------------------------------------------------------------------------------------------------------------------------------------- ###

def process_region(browser, row: dict, commit: bool, offered: dict = None) -> tuple:
    """
    Prints one map region and returns (report, the wizard options it saw), so the next
    row in the state can be checked against those before its pages are opened.
    """
    report = {"context": row_context(row), "cd": row["cd"],
              "map_region_name": row["map_region_name"]}
    choices = wizard_choices(row)

    if offered:
        problems = missing_wizard_values(choices, offered)
        if problems:
            raise ValueError(" ".join(problems))

    browser.open_folder(row["folder_id"], row["map_region_name"])
    # By id, not just name: VAN lets two regions in a folder share one, and dbt
    # picked which of them the row means.
    region = browser.find_map_region(row["map_region_name"], row["map_region_id"])

    browser.open_print_region(region, expected_name=row["map_region_name"])

    seen = browser.print_wizard_options()
    problems = missing_wizard_values(choices, seen)
    if problems:
        raise ValueError(" ".join(problems))

    applied = browser.fill_print_wizard(choices)
    report["choices"] = applied

    if choices.get("minivan_campaign"):
        overrides = check_campaign_did_not_override(applied, choices)
        if overrides:
            raise ValueError(" ".join(overrides))

    # Last, so a dropdown's postback cannot put the plain region name back.
    report["title"] = browser.set_print_title(pdf_title(row))

    if not commit:
        report["action"] = "dry run"
        return report, seen

    report["wait_page"] = browser.submit_print_wizard()
    report["action"] = "printed"
    return report, seen

### -------------------------------------------------------------------------------------------------------------------------------------------- ###

def process_state(browser, state: str, rows: list, commit: bool, pause: bool = False) -> tuple:
    """
    Prints every mapped region in one state and returns (reports, failures). A session
    switch that fails is recorded once per row, so the summary still names them all.
    """
    reports = []
    failures = []

    try:
        browser.switch_session(state, committee_for(state), database=MY_VOTERS)
    except Exception as session_error:
        print(f"Failed: {session_error}", flush=True)
        return reports, [{
            "stage": "Session",
            "context": row_context(row),
            "error": f"Could not switch the session into {state}: {session_error}",
            "artifacts": browser.capture(f"{state}_session"),
        } for row in rows]

    offered = None

    for index, row in enumerate(rows, start=1):
        progress(index, len(rows), row_context(row))

        try:
            report, offered = process_region(browser, row, commit, offered)
            reports.append(report)
            print(f"{report['action']}: {report['title']}", flush=True)
        except Exception as region_error:
            print(f"Failed: {region_error}", flush=True)
            failures.append({
                "stage": "Print",
                "context": row_context(row),
                "error": str(region_error),
                "where": where_it_failed(region_error),
                "artifacts": browser.capture(f"{row['cd']}_{row['map_region_name']}"),
            })

        if pause:
            pause_for_review(row_context(row))

    return reports, failures

### -------------------------------------------------------------------------------------------------------------------------------------------- ###

def print_packets(commit: bool = True, headless: bool = True, stored_login: bool = True,
                  only: list = None, region: list = None, test_district: bool = False,
                  pause: bool = False) -> list:
    """
    Drives the print wizard for every mapped region.

    Runs after van_refresh_packets in the chain, so the turf is fresh by the time this
    starts; a refresh that failed stops the chain before this job runs.
    """
    pipeline_errors = []
    rows = load_mapped_packets(only, region, test_district)

    print(f"\n[{'COMMIT' if commit else 'DRY RUN'}] printing {len(rows)} map regions "
          f"across {len(by_state(rows))} states.")

    reports = []

    if rows:
        driver, browser = open_session(headless, stored_login)
        try:
            for state, in_state in by_state(rows).items():
                print(f"\n=== {state} ===", flush=True)
                state_reports, state_failures = process_state(
                    browser, state, in_state, commit, pause
                )
                reports += state_reports
                pipeline_errors += state_failures
        finally:
            driver.quit()

    return finish("print", reports, pipeline_errors)

### -------------------------------------------------------------------------------------------------------------------------------------------- ###

def main():
    parser = argparse.ArgumentParser(
        description="Print the turf packets on the Packet Tracker sheet"
    )
    add_login_arguments(parser)
    parser.add_argument("--commit", action="store_true",
                        help="Actually print. Without it, nothing is written.")
    parser.add_argument("--only", nargs="+", metavar="CD",
                        help="Only these districts, e.g. --only ST-01 ST-02.")
    parser.add_argument("--region", nargs="+", metavar="NAME",
                        help="Only these map regions, by name.")
    parser.add_argument("--test-district", action="store_true",
                        help=f"Print the {TEST_STATE} test region instead of reading the sheet.")
    parser.add_argument("--pause", action="store_true",
                        help="Wait for Enter after each region, to read it on screen.")
    args = parser.parse_args()
    check_login_arguments(parser, args)

    if args.pause and args.headless:
        parser.error("--pause needs a browser to look at, so it cannot be used with --headless.")

    print_packets(args.commit, args.headless, args.stored_login, args.only, args.region,
                  args.test_district, args.pause)


if __name__ == "__main__":
    main()