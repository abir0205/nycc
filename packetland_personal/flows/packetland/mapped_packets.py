"""
The Packet Tracker rows the packet flows work on, and what they share.

Reads the Packet Tracker sheet directly and checks every row against VAN before a flow
touches it, which is the filtering the national version left to dbt. Also holds the
pieces every flow needs: how a row is named in reports, how it matches what VAN returns,
how a VAN timestamp is read, and the failure summary.

Everything you have to fill in for your own committee is in YOUR SETTINGS below, apart
from the bot's login (van_browser_utilities.py) and the committee name (target_slots.py).
"""

import json
from datetime import datetime, timezone
from zoneinfo import ZoneInfo
from pathlib import Path
from core_utilities.gcp_utilities import get_secret
from core_utilities.gsheet_utilities import read_sheet_values
from core_utilities.van_utilities import fetch_folders, fetch_map_regions, get_van_auth_for_state
from flows.van_automation.session import report_summary
from flows.van_automation.target_slots import by_state

### -------------------------------------------------------------------------------------------------------------------------------------------- ###
## YOUR SETTINGS -------------------------------------------------------------------------------------------------------------------------------- ###
### -------------------------------------------------------------------------------------------------------------------------------------------- ###

# Every row runs in this state, so the session is switched once per run.
STATE = "NY"

# The Google service account JSON key, stored whole in separate foler. It reads the
# Packet Tracker and writes the PDFs to Drive, so share both with its client_email.
RESOURCES = Path(__file__).resolve().parent / "resources"

SERVICE_ACCOUNT_SECRET = str(RESOURCES / "packet-bot-key.json")

# The VAN API key, stored as {"NY": {"app_name": "...", "app_key": "..."}}. It needs the
# extended permission: a standard key reads folders and regions but VAN answers the
# refresh with 403. Every folder on the sheet has to be shared with this API user.
VAN_SECRET = str(RESOURCES / "van.json")
VAN_DATABASE = "MyVoters"

# The Packet Tracker: the id from the sheet's URL, and the tab the rows are on.
PACKET_TRACKER_SHEET_ID = "[GOOGLE SHEET URL]"
PACKET_TRACKER_TAB = "Packet Tracker"

# What each field is called in the sheet's header row. Change the right-hand side to
# match your headers; matching ignores case and surrounding spaces. cd is whatever you
# group packets by (a district, a region): it names the Drive folder and is what --only
# filters on. minivan_campaign is the one optional column.
SHEET_COLUMNS = {
    "cd": "cd",
    "folder_id": "folder_id",
    "map_region_id": "map_region_id",
    "map_region_name": "map_region_name",
    "report_format": "report_format",
    "script": "script",
    "minivan_campaign": "minivan_campaign",
}

# Packets / <cd> / <Saturday> / <region>.pdf
PACKETS_DRIVE_FOLDER_ID = """GOOGLE SHEETS URL"""

# A throwaway region for --test-district, written out here rather than read from the
# sheet. Cut it by hand in a folder you control. With a MiniVAN campaign, the script has
# to be that campaign's default, or the print flow's override check fails the row.
TEST_STATE = STATE
TEST_DISTRICT = "TEST"
TEST_ROWS = [{
    "cd": TEST_DISTRICT,
    "folder_id": "TEST",
    "map_region_id" :"TEST",
    "map_region_name":"TEST",
    "report_format":"TEST",
    "script":"TEST",
    "minivan_campaign":"TEST",
}]

### -------------------------------------------------------------------------------------------------------------------------------------------- ###
## MAPPED PACKETS ------------------------------------------------------------------------------------------------------------------------------- ###
### -------------------------------------------------------------------------------------------------------------------------------------------- ###

OPTIONAL_FIELDS = ["minivan_campaign"]
REQUIRED_FIELDS = [field for field in SHEET_COLUMNS if field not in OPTIONAL_FIELDS]
ID_FIELDS = ["folder_id", "map_region_id"]

# How many of a folder's regions an error lists before it stops.
LISTED_REGIONS = 12

# The rows the last load set aside, so finish can name them again at the end of the log.
_skipped = []

### -------------------------------------------------------------------------------------------------------------------------------------------- ###

def row_context(row: dict) -> str:
    """How a row is named everywhere it is reported: 'NY-12 / Brooklyn Doors / Region 12'."""

    return f"{row['cd']} / {row.get('folder_name') or 'folder ' + str(row['folder_id'])} / {row['map_region_name']}"

### -------------------------------------------------------------------------------------------------------------------------------------------- ###

def row_label(row: dict) -> str:
    """Where a row came from, for the rows set aside before a run starts."""

    source = f"sheet row {row['sheet_row']}" if row.get("sheet_row") else "TEST_ROWS"
    return f"{source} ({row.get('cd') or 'no cd'} / {row.get('map_region_name') or 'no region name'})"

### -------------------------------------------------------------------------------------------------------------------------------------------- ###

def clean_cell(value) -> str:
    """A cell as trimmed text. Numbers come back as numbers, so 123.0 reads as '123'."""

    if value is None:
        return ""
    if isinstance(value, float) and value.is_integer():
        value = int(value)

    return str(value).strip()

### -------------------------------------------------------------------------------------------------------------------------------------------- ###

def read_packet_tracker() -> list:
    """
    The sheet's rows as dicts keyed by field, with the sheet row number they came from.
    Blank rows are dropped. A missing required column is a broken sheet, so it raises.
    """
    tab_range = "'" + PACKET_TRACKER_TAB.replace("'", "''") + "'"
    values = read_sheet_values(PACKET_TRACKER_SHEET_ID, tab_range, SERVICE_ACCOUNT_SECRET)

    if not values:
        raise RuntimeError(f"The Packet Tracker tab {PACKET_TRACKER_TAB!r} is empty.")

    header = [clean_cell(cell).lower() for cell in values[0]]
    positions = {}
    for field, column in SHEET_COLUMNS.items():
        if column.strip().lower() in header:
            positions[field] = header.index(column.strip().lower())

    missing = [SHEET_COLUMNS[field] for field in REQUIRED_FIELDS if field not in positions]
    if missing:
        raise RuntimeError(
            f"The Packet Tracker has no {', '.join(missing)} column. Its header row has: "
            f"{', '.join(cell for cell in header if cell) or 'nothing'}. "
            "Fix the header or SHEET_COLUMNS in mapped_packets.py."
        )

    rows = []
    for sheet_row, cells in enumerate(values[1:], start=2):
        # The API leaves off trailing blank cells, so a short row is padded, not misread.
        row = {field: clean_cell(cells[index]) if index < len(cells) else ""
               for field, index in positions.items()}
        for field in OPTIONAL_FIELDS:
            row.setdefault(field, "")
        if not any(row.values()):
            continue
        row["sheet_row"] = sheet_row
        rows.append(row)

    return rows

### -------------------------------------------------------------------------------------------------------------------------------------------- ###

def sheet_problems(row: dict) -> list:
    """What is wrong with a row on the sheet's own terms, before VAN is asked anything."""

    problems = [f"{SHEET_COLUMNS[field]} is blank" for field in REQUIRED_FIELDS if not row.get(field)]
    problems += [
        f"{SHEET_COLUMNS[field]} {row[field]!r} is not a VAN id"
        for field in ID_FIELDS if row.get(field) and not row[field].isdigit()
    ]

    return problems

### -------------------------------------------------------------------------------------------------------------------------------------------- ###

def check_against_van(rows: list, skipped: list) -> list:
    """
    Keeps the rows whose region exists in VAN under the id and name the sheet gives it.

    One call for the folder names and one per folder for its regions. The print flow
    finds a region by name and id and the download flow matches a PDF by name, so both
    have to agree with VAN; a name that does not match usually means the id is wrong.
    """
    van_auth = get_van_auth_for_state(get_secret(VAN_SECRET), STATE, VAN_DATABASE)
    folder_names = {str(folder.get("folderId")): folder.get("name") for folder in fetch_folders(van_auth)}

    regions_by_folder = {}
    folder_errors = {}
    for folder_id in dict.fromkeys(row["folder_id"] for row in rows):
        try:
            regions_by_folder[folder_id] = {
                str(region.get("mapRegionId")): (region.get("name") or "").strip()
                for region in fetch_map_regions(folder_id, van_auth)
            }
        except Exception as fetch_error:
            message = str(fetch_error).strip().splitlines()
            folder_errors[folder_id] = message[0] if message else type(fetch_error).__name__

    kept = []
    for row in rows:
        folder_id = row["folder_id"]
        if folder_id in folder_errors:
            skipped.append((row, f"VAN would not list folder {folder_id}, so it is probably not "
                                 f"shared with the API user: {folder_errors[folder_id]}"))
            continue

        regions = regions_by_folder[folder_id]
        van_name = regions.get(row["map_region_id"])
        if van_name is None:
            listed = ", ".join(f"{region_id} {name!r}"
                               for region_id, name in list(regions.items())[:LISTED_REGIONS])
            more = f" and {len(regions) - LISTED_REGIONS} more" if len(regions) > LISTED_REGIONS else ""
            skipped.append((row, f"folder {folder_id} has no map region {row['map_region_id']}. "
                                 f"It holds: {listed + more or 'no map regions'}"))
            continue
        if van_name != row["map_region_name"]:
            skipped.append((row, f"map region {row['map_region_id']} is named {van_name!r} in VAN, "
                                 f"not {row['map_region_name']!r}"))
            continue

        row["folder_name"] = folder_names.get(folder_id) or f"folder {folder_id}"
        kept.append(row)

    return kept

### -------------------------------------------------------------------------------------------------------------------------------------------- ###

def load_mapped_packets(only: list = None, region: list = None, test_district: bool = False) -> list:
    """
    Returns the Packet Tracker rows to refresh, print or download, as dicts.

    A row wrong on the sheet's own terms, a repeat of an earlier row, or a region VAN does
    not have under that id and name is set aside with a warning rather than failing the
    run, the way the national version's dbt filter did. Set-aside rows are named again
    in the summary at the end.

    Args:
        only (list): Optional cd values to narrow to, e.g. ['NY-12']. Case does not matter.
        region (list): Optional map region names to narrow to.
        test_district (bool): Use TEST_ROWS instead of the sheet.
    Returns:
        list: One dict per row, each carrying the state its session needs.
    Raises:
        RuntimeError: If the sheet is empty or nothing is left to run, which is a broken
            sheet rather than a quiet day.
    """
    _skipped.clear()

    if test_district:
        print("Test run: TEST_ROWS in mapped_packets.py rather than the sheet.")
        rows = [{"sheet_row": None, **{field: clean_cell(value) for field, value in row.items()}}
                for row in TEST_ROWS]
        for row in rows:
            for field in OPTIONAL_FIELDS:
                row.setdefault(field, "")
    else:
        rows = read_packet_tracker()

    if not rows:
        raise RuntimeError("The Packet Tracker has no rows; refusing to run with nothing to print.")

    wanted_cds = {cd.strip().upper() for cd in only} if only else None
    wanted_regions = {name.strip() for name in region} if region else None
    rows = [row for row in rows
            if (not wanted_cds or row["cd"].upper() in wanted_cds)
            and (not wanted_regions or row["map_region_name"] in wanted_regions)]

    if not rows:
        raise RuntimeError(
            "No rows left after filtering. Check --only and --region against the sheet."
        )

    skipped = []
    checked = []
    seen = {}
    for row in rows:
        problems = sheet_problems(row)
        if problems:
            skipped.append((row, "; ".join(problems)))
            continue

        # The same region twice would print twice, giving it two list numbers.
        key = (row["folder_id"], row["map_region_id"])
        if key in seen:
            skipped.append((row, f"the same region as {row_label(seen[key])}"))
            continue
        seen[key] = row
        checked.append(row)

    kept = check_against_van(checked, skipped) if checked else []

    for row in kept:
        row["state"] = STATE

    _skipped.extend(skipped)
    if skipped:
        print(f"\nSkipping {len(skipped)} rows:")
        for row, reason in skipped:
            print(f"  {row_label(row)}: {reason}")

    if not kept:
        raise RuntimeError(
            "No rows left to run: every row was skipped (reasons above). "
            + ("Fill in TEST_ROWS in mapped_packets.py." if test_district else "Fix them on the sheet.")
        )

    # The PDF job list shows titles only, so regions sharing a name share the newest print.
    names = {}
    for row in kept:
        names.setdefault(row["map_region_name"], []).append(row)
    for name, same in names.items():
        if len(same) > 1:
            print(f"Warning: {', '.join(row_label(row) for row in same)} share the name {name!r}, "
                  "so the download files the newest print of that name for each.")

    print(f"Found {len(kept)} mapped packets to run in {STATE}.")
    return kept

### -------------------------------------------------------------------------------------------------------------------------------------------- ###

VAN_TIMEZONE = ZoneInfo("America/New_York")


def parse_van_timestamp(timestamp: str) -> datetime | None:
    """A VAN timestamp as an aware UTC datetime, or None for a blank one.

    VAN sends the Eastern wall clock with a Z on the end: a refresh that landed at
    16:17 Eastern comes back as 2026-09-17T16:17:00Z. The offset is dropped and the
    wall clock read as Eastern, whatever VAN claims."""
    if not timestamp:
        return None

    wall_clock = datetime.fromisoformat(timestamp.replace("Z", "+00:00")).replace(tzinfo = None)

    return wall_clock.replace(tzinfo = VAN_TIMEZONE).astimezone(timezone.utc)

### -------------------------------------------------------------------------------------------------------------------------------------------- ###

def region_key(row: dict) -> tuple:
    """How a row is keyed against what VAN returns."""

    return (row["state"], row["folder_id"], str(row["map_region_id"]))

### -------------------------------------------------------------------------------------------------------------------------------------------- ###

def finish(what: str, reports: list, pipeline_errors: list) -> list:
    """Prints the summary every pass ends with, and raises if anything failed."""

    # report_summary names a failure by its slot; these flows call it the context.
    report_summary(what, reports, [
        {**error, "slot": f"{error['context']} [{error['stage']}]"} for error in pipeline_errors
    ])

    if _skipped:
        print(f"\n{len(_skipped)} sheet rows were skipped before the {what} pass:")
        for row, reason in _skipped:
            print(f"  {row_label(row)}: {reason}")

    if pipeline_errors:
        print("\n=== PIPELINE FAILURE LOG SUMMARY ===")
        print(json.dumps(pipeline_errors, indent=2, default=str))
        raise RuntimeError(
            f"{len(pipeline_errors)} map regions failed the {what} pass. "
            f"Regions: {', '.join(error['context'] for error in pipeline_errors)}"
        )

    return reports

### -------------------------------------------------------------------------------------------------------------------------------------------- ###
