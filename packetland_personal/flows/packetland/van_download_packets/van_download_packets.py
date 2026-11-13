"""
Downloads the turf packet PDFs VAN printed and files them in Drive by district and weekend.

Printing only queues a PDF in VAN. This reads each state's PDF Print Jobs list, matches the
newest job per Packet Tracker region by title, and uploads it. It runs after
van_print_packets in the chain, but reads nothing the print flow hands it, so it can be
rerun alone.

The title it looks for is the one the print flow gives the PDF, the region name with its
id on the end (pdf_title). A region printed by hand, or before Oct 14 2026, is titled with
its name alone and is not found.

Only some pages go to Drive: page 1, then every third page after it (4, 7, 10 ...), as
keep_page says. --all-pages files the whole PDF instead.
"""

import argparse
import glob
import io
import json
import os
import re
import time
from datetime import datetime, timedelta, timezone

from pypdf import PdfReader, PdfWriter

from core_utilities.gsheet_utilities import (
    auth_to_drive,
    find_drive_file,
    find_or_create_folder,
    upload_file_to_drive_folder,
)
from core_utilities.van_browser_utilities import MY_VOTERS
from flows.packetland.mapped_packets import (
    PACKETS_DRIVE_FOLDER_ID,
    SERVICE_ACCOUNT_SECRET,
    STATE,
    TEST_STATE,
    VAN_TIMEZONE,
    finish,
    load_mapped_packets,
    row_context,
)
from flows.packetland.van_print_packets.van_print_packets import pdf_title
from flows.van_automation.constants import (
    ARTIFACT_DIR,
    PDF_DOWNLOAD_URL,
    PDF_JOB_CREATED_FORMAT,
    PDF_JOB_MAP_REGION_TYPE,
)
from flows.van_automation.session import (
    add_login_arguments,
    check_login_arguments,
    open_session,
)
from flows.van_automation.target_slots import by_state, committee_for

### -------------------------------------------------------------------------------------------------------------------------------------------- ###
## VAN DOWNLOAD PACKETS FLOW -------------------------------------------------------------------------------------------------------------------- ###
### -------------------------------------------------------------------------------------------------------------------------------------------- ###

# The Drive folder and the service account that writes to it are set in mapped_packets.py.

# Without --since, a print from the last day counts, which covers the chain's own print.
DEFAULT_LOOKBACK = timedelta(hours=24)

# How long the whole run waits on PDFs VAN is still rendering, and how often it looks.
DEFAULT_WAIT_MINUTES = 30
POLL_SECONDS = 30

PDF_SIGNATURE = b"%PDF"

# Which pages of each PDF go to Drive, counting from 1: page 1, then every third page
# after it, so 1, 4, 7, 10 and on. Change this to keep a different set, and change
# PAGE_RULE with it: a rerun replaces any file filed under a different rule.
PAGE_RULE = "1, then every third page"


def keep_page(page: int) -> bool:
    return (page - 1) % 3 == 0


# Stamped on each Drive file, so a rerun knows which print and which pages it holds.
JOB_PROPERTY = "van_job_id"
PAGES_PROPERTY = "pages_kept"
ALL_PAGES = "all pages"

### -------------------------------------------------------------------------------------------------------------------------------------------- ###

def weekend_folder_name(now: datetime) -> str:
    """The Saturday the packets are for, as 2026-10-03. A weekend run files to that weekend."""

    today = now.astimezone(VAN_TIMEZONE).date()
    if today.weekday() == 6:
        saturday = today - timedelta(days=1)
    else:
        saturday = today + timedelta(days=(5 - today.weekday()) % 7)

    return saturday.isoformat()

### -------------------------------------------------------------------------------------------------------------------------------------------- ###

def parse_since(value: str) -> datetime:
    """--since as an aware UTC datetime. A date or timestamp with no zone is read as Eastern."""

    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=VAN_TIMEZONE)

    return parsed.astimezone(timezone.utc)

### -------------------------------------------------------------------------------------------------------------------------------------------- ###

def parse_created(created: str, offset_minutes: int | None) -> datetime:
    """
    A job's Created cell, shown in the state's time to the minute, as an aware UTC datetime.
    With no offset off the page, it is read as Eastern, which every state this runs for is on.
    """

    wall_clock = datetime.strptime(created.strip(), PDF_JOB_CREATED_FORMAT)
    if offset_minutes is None:
        state_zone = VAN_TIMEZONE
    else:
        state_zone = timezone(timedelta(minutes=offset_minutes))

    return wall_clock.replace(tzinfo=state_zone).astimezone(timezone.utc)

### -------------------------------------------------------------------------------------------------------------------------------------------- ###

def scan_job_list(browser, waiting: dict, since: datetime) -> tuple:
    """
    Walks the job list newest first and returns ({title: job}, titles still rendering).

    The first row for a title since the cutoff is its newest print, and that one decides:
    if it has no download link yet it is still rendering, and an older finished copy of
    the same title is not taken in its place. Stops paging once every title is settled
    or the list is older than the cutoff.
    """
    found = {}
    rendering = set()

    # The page count is on the pager, so page 1 is read before the loop knows its range.
    browser.open_pdf_job_list()
    listing = browser.pdf_job_rows()
    if listing["offset_minutes"] is None and not scan_job_list.warned:
        print(f"Note: {listing['offset_problem']}, so Created times are read as Eastern.",
              flush=True)
        scan_job_list.warned = True

    for page in range(1, listing["pages"] + 1):
        if page > 1:
            browser.pdf_job_page(page)
            listing = browser.pdf_job_rows()

        for job in listing["rows"]:
            # Newest first, so the first row past the cutoff means every row after it is too.
            if parse_created(job["created"], listing["offset_minutes"]) < since:
                return found, rendering

            title = job["title"]
            if job["type"] != PDF_JOB_MAP_REGION_TYPE:
                continue
            if title not in waiting or title in found or title in rendering:
                continue

            if job["href"]:
                found[title] = job
            else:
                rendering.add(title)

        if len(found) + len(rendering) == len(waiting):
            break

    return found, rendering

# The Eastern note prints once a run, not on every poll.
scan_job_list.warned = False

### -------------------------------------------------------------------------------------------------------------------------------------------- ###

def weekend_folder_id(row: dict, weekend: str, drive_service, folder_cache: dict) -> str:
    """The Drive folder for this district and weekend, made the first time it is needed."""

    if row["cd"] not in folder_cache:
        district_folder = find_or_create_folder(row["cd"], PACKETS_DRIVE_FOLDER_ID, drive_service)
        folder_cache[row["cd"]] = find_or_create_folder(weekend, district_folder, drive_service)

    return folder_cache[row["cd"]]

### -------------------------------------------------------------------------------------------------------------------------------------------- ###

def trim_pages(pdf: bytes) -> tuple:
    """The PDF with only the pages keep_page wants, as (bytes, pages kept, pages in all)."""

    reader = PdfReader(io.BytesIO(pdf))
    total = len(reader.pages)
    writer = PdfWriter()
    for number, page in enumerate(reader.pages, start=1):
        if keep_page(number):
            writer.add_page(page)

    out = io.BytesIO()
    writer.write(out)
    return out.getvalue(), len(writer.pages), total

### -------------------------------------------------------------------------------------------------------------------------------------------- ###

def filed_as(file_id: str, drive_service) -> dict:
    """The job and page stamps on a Drive file this flow uploaded, or {} for one it did not."""

    found = drive_service.files().get(
        fileId=file_id, fields="appProperties", supportsAllDrives=True
    ).execute(num_retries=3)
    return found.get("appProperties") or {}

### -------------------------------------------------------------------------------------------------------------------------------------------- ###

def deliver(browser, row: dict, job: dict, weekend: str, drive_service, folder_cache: dict,
            dry_run: bool, all_pages: bool = False) -> dict:
    """Downloads one region's PDF, keeps the pages it should, and files it in Drive."""

    report = {"context": row_context(row), "cd": row["cd"],
              "map_region_name": row["map_region_name"], "title": job["title"],
              "job_id": job["job_id"], "created": job["created"]}

    if dry_run:
        report["action"] = "found"
        return report

    folder_id = weekend_folder_id(row, weekend, drive_service, folder_cache)
    file_name = f"{pdf_title(row)}.pdf"
    pages_wanted = ALL_PAGES if all_pages else PAGE_RULE

    # A rerun finds most packets already filed. The stamps say whether the file is this
    # print with these pages; a file without them predates trimming and is replaced.
    existing = find_drive_file(file_name, folder_id, drive_service)
    if existing:
        stamps = filed_as(existing["id"], drive_service)
        if stamps.get(JOB_PROPERTY) == str(job["job_id"]) and stamps.get(PAGES_PROPERTY) == pages_wanted:
            report["action"] = "already in Drive"
            report["drive_file_id"] = existing["id"]
            return report

    pdf = browser.download(f"{PDF_DOWNLOAD_URL}?jobID={job['job_id']}")
    if not pdf.startswith(PDF_SIGNATURE):
        raise ValueError(
            f"Job {job['job_id']} came back as {len(pdf)} bytes that are not a PDF, "
            f"starting {pdf[:60]!r}. The login may have lapsed."
        )

    if all_pages:
        report["pages"] = f"all {len(PdfReader(io.BytesIO(pdf)).pages)}"
    else:
        pdf, kept, total = trim_pages(pdf)
        report["pages"] = f"{kept} of {total}"

    file_id = upload_file_to_drive_folder(file_name, pdf, folder_id, drive_service, "application/pdf")
    drive_service.files().update(
        fileId=file_id, supportsAllDrives=True,
        body={"appProperties": {JOB_PROPERTY: str(job["job_id"]), PAGES_PROPERTY: pages_wanted}},
    ).execute(num_retries=3)

    report["drive_file_id"] = file_id
    report["action"] = "uploaded"
    return report

### -------------------------------------------------------------------------------------------------------------------------------------------- ###

def process_state(browser, state: str, rows: list, since: datetime, deadline: float,
                  weekend: str, drive_service, folder_cache: dict, dry_run: bool,
                  all_pages: bool = False) -> tuple:
    """
    Downloads every mapped region's PDF in one state and returns (reports, failures).
    Walks the job list again every POLL_SECONDS while anything is missing, until the deadline.
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
        } for row in rows]

    # Keyed by the title the print flow gave each PDF. It carries the region id, so two
    # regions sharing a name still wait on their own PDFs.
    waiting = {pdf_title(row): row for row in rows}

    rendering = set()
    while waiting:
        try:
            found, rendering = scan_job_list(browser, waiting, since)
        except Exception as scan_error:
            print(f"Failed: {scan_error}", flush=True)
            failures += [{"stage": "Job List", "context": row_context(row),
                          "error": str(scan_error)} for row in waiting.values()]
            return reports, failures

        for title, job in found.items():
            row = waiting.pop(title)
            try:
                report = deliver(browser, row, job, weekend, drive_service, folder_cache, dry_run,
                                 all_pages)
                reports.append(report)
                pages = f" ({report['pages']} pages)" if report.get("pages") else ""
                print(f"{report['action']}: {title}{pages}", flush=True)
            except Exception as download_error:
                print(f"Failed: {title}: {download_error}", flush=True)
                failures.append({"stage": "Download", "context": row_context(row),
                                 "error": str(download_error)})

        if not waiting or time.monotonic() >= deadline:
            break

        print(f"{len(waiting)} PDFs in {state} not ready ({len(rendering)} still rendering). "
              f"Looking again in {POLL_SECONDS}s.", flush=True)
        time.sleep(POLL_SECONDS)

    for title, row in waiting.items():
        failures.append({
            "stage": "Find",
            "context": row_context(row),
            "error": (f"{title!r} was still rendering when the wait ran out."
                      if title in rendering else
                      f"No PDF titled {title!r} printed since {since.isoformat()}."),
        })

    return reports, failures

### -------------------------------------------------------------------------------------------------------------------------------------------- ###

def rows_from_print_summary(source: str, only: list = None, region: list = None) -> list:
    """
    The regions a print run printed, read from its summary file rather than the sheet, so
    the download fetches what was printed whatever the Queue holds now. source is the
    summary's path, or 'latest' for the newest print summary in ARTIFACT_DIR. --only and
    --region narrow it the same way they narrow the sheet.
    """
    if source == "latest":
        summaries = glob.glob(os.path.join(ARTIFACT_DIR, "*_print_summary.txt"))
        if not summaries:
            raise RuntimeError(f"No print summary in {ARTIFACT_DIR} to download from.")
        source = max(summaries, key=os.path.getmtime)

    with open(source) as handle:
        reports = json.load(handle).get("reports", [])

    wanted_cds = {cd.strip().upper() for cd in only} if only else None
    wanted_regions = {name.strip() for name in region} if region else None

    rows = []
    seen = set()
    for report in reports:
        if report.get("action") != "printed" or report.get("title") in seen:
            continue
        # The title ends in the region id: 'name (1028077)', or 'name(1028077)' when shortened.
        region_id = re.search(r"\((\d+)\)$", report.get("title", ""))
        parts = report.get("context", "").split(" / ")
        if not region_id or len(parts) < 3:
            print(f"Skipping a summary entry that cannot be read: {report.get('context') or report}")
            continue
        row = {"cd": report["cd"], "folder_id": "", "folder_name": parts[1],
               "map_region_name": report["map_region_name"], "map_region_id": region_id.group(1),
               "state": STATE}
        if wanted_cds and row["cd"].upper() not in wanted_cds:
            continue
        if wanted_regions and row["map_region_name"] not in wanted_regions:
            continue
        seen.add(report["title"])
        rows.append(row)

    if not rows:
        raise RuntimeError(f"{source} has no printed regions" +
                           (" matching --only/--region." if only or region else "."))

    print(f"Downloading the {len(rows)} regions printed in {source}, not the sheet's Queue.")
    return rows

### -------------------------------------------------------------------------------------------------------------------------------------------- ###

def download_packets(dry_run: bool = False, headless: bool = True, stored_login: bool = True,
                     only: list = None, region: list = None, test_district: bool = False,
                     since: datetime = None, wait_minutes: int = DEFAULT_WAIT_MINUTES,
                     all_pages: bool = False, from_print: str = None,
                     date_folder: str = None) -> list:
    """
    Files every mapped region's newest PDF in Drive under <district> / <weekend>, with only
    the pages keep_page wants unless all_pages.

    The wait is for the whole run, not per state: the print flow has queued every state's
    PDFs by the time this starts, and VAN renders them all at once.

    With from_print (a print summary's path, or 'latest'), the regions come from what that
    print run printed instead of the Packet Tracker.

    With date_folder, the PDFs go into a folder by that name under each district instead of
    the coming Saturday's.
    """
    now = datetime.now(timezone.utc)
    since = since or now - DEFAULT_LOOKBACK
    weekend = (date_folder or "").strip() or weekend_folder_name(now)
    deadline = time.monotonic() + wait_minutes * 60

    pipeline_errors = []
    reports = []
    if from_print:
        rows = rows_from_print_summary(from_print, only, region)
    else:
        rows = load_mapped_packets(only, region, test_district)

    print(f"\n[{'DRY RUN' if dry_run else 'COMMIT'}] downloading PDFs printed since "
          f"{since.astimezone(VAN_TIMEZONE):%Y-%m-%d %H:%M} Eastern for {len(rows)} map "
          f"regions across {len(by_state(rows))} states, into the {weekend} folders, "
          f"keeping {ALL_PAGES if all_pages else 'pages ' + PAGE_RULE}.")

    drive_service = None if dry_run else auth_to_drive(SERVICE_ACCOUNT_SECRET)
    folder_cache = {}

    driver, browser = open_session(headless, stored_login)
    try:
        for state, in_state in by_state(rows).items():
            print(f"\n=== {state} ===", flush=True)
            state_reports, state_failures = process_state(
                browser, state, in_state, since, deadline, weekend,
                drive_service, folder_cache, dry_run, all_pages,
            )
            reports += state_reports
            pipeline_errors += state_failures
    finally:
        driver.quit()

    return finish("download", reports, pipeline_errors)

### -------------------------------------------------------------------------------------------------------------------------------------------- ###

def main():
    parser = argparse.ArgumentParser(
        description="Download the printed turf packets into Drive"
    )
    add_login_arguments(parser)
    parser.add_argument("--dry-run", action="store_true",
                        help="Find each region's PDF and report it, without downloading.")
    parser.add_argument("--only", nargs="+", metavar="CD",
                        help="Only these districts, e.g. --only ST-01 ST-02.")
    parser.add_argument("--region", nargs="+", metavar="NAME",
                        help="Only these map regions, by name.")
    parser.add_argument("--test-district", action="store_true",
                        help=f"Download the {TEST_STATE} test region instead of reading the sheet.")
    parser.add_argument("--since", type=parse_since,
                        help="Only PDFs printed at or after this, e.g. 2026-09-26 or "
                             "'2026-09-26 18:00', Eastern. Defaults to a day ago.")
    parser.add_argument("--wait-minutes", type=int, default=DEFAULT_WAIT_MINUTES,
                        help="How long to wait on PDFs still rendering. 0 takes what is there.")
    parser.add_argument("--all-pages", action="store_true",
                        help=f"File every page, rather than pages {PAGE_RULE}.")
    parser.add_argument("--date-folder", metavar="NAME",
                        help="File this run's PDFs in a folder by this name under each district, "
                             "e.g. 10-30-26, instead of the coming Saturday's date.")
    parser.add_argument("--from-print", metavar="SUMMARY",
                        help="Download what a print run printed, from its summary file in "
                             f"{ARTIFACT_DIR}, instead of what the sheet's Queue holds now. "
                             "'latest' takes the newest print summary.")
    args = parser.parse_args()
    check_login_arguments(parser, args)

    download_packets(args.dry_run, args.headless, args.stored_login, args.only, args.region,
                     args.test_district, args.since, args.wait_minutes, args.all_pages,
                     args.from_print, args.date_folder)


if __name__ == "__main__":
    main()