"""
Lists the map regions your VAN API key can see, laid out like the Packet Tracker.

Run it from the packetland_2 folder:

    python list_regions.py                          every folder the key can see
    python list_regions.py --folder SL-R12          folders whose name contains SL-R12
    python list_regions.py --folder 85517 SL-R05    a folder by ID, plus folders matching SL-R05

It writes packet_tracker_rows.csv next to this file, with the Packet Tracker's columns in
its order. Copy the rows you want this round onto the Packet Tracker sheet. It never writes
to the sheet itself, since every row there gets printed on the next run.
"""

import argparse
import csv
from pathlib import Path

from core_utilities.gcp_utilities import get_secret
from core_utilities.van_utilities import fetch_folders, fetch_map_regions, get_van_auth_for_state
from flows.packetland.mapped_packets import SHEET_COLUMNS, STATE, VAN_DATABASE, VAN_SECRET

OUTPUT = Path(__file__).resolve().parent / "packet_tracker_rows.csv"

# Filled into every row. Leave blank to fill in on the sheet. They must match VoteBuilder's
# dropdown labels exactly, and with a MiniVAN campaign the script must be its default.
DEFAULT_REPORT_FORMAT = ""
DEFAULT_SCRIPT = ""
DEFAULT_MINIVAN_CAMPAIGN = ""

# The cd column names each packet's Drive folder (Packets / cd / Saturday / region.pdf).
# By default it is the VAN folder's name; set this to False to leave it blank instead.
CD_FROM_FOLDER_NAME = True

# Fields in the Packet Tracker's order, then folder_name to help pick rows. The scripts
# ignore columns they don't use, so folder_name can stay on the sheet.
FIELDS = ["cd", "folder_id", "map_region_id", "map_region_name",
          "report_format", "script", "minivan_campaign"]
EXTRA = ["folder_name"]


def wanted(folder: dict, filters: list) -> bool:
    """A folder matches a filter by exact ID, or by name containing it (any case)."""
    if not filters:
        return True
    folder_id = str(folder.get("folderId"))
    name = (folder.get("name") or "").lower()
    return any(f == folder_id if f.isdigit() else f.lower() in name for f in filters)


def main():
    parser = argparse.ArgumentParser(description="List map regions in the Packet Tracker's layout.")
    parser.add_argument("--folder", nargs="+", metavar="ID_OR_NAME",
                        help="Only these folders: an ID, or part of a folder name.")
    args = parser.parse_args()

    auth = get_van_auth_for_state(get_secret(VAN_SECRET), STATE, VAN_DATABASE)
    folders = [f for f in fetch_folders(auth) if wanted(f, args.folder)]
    if args.folder and not folders:
        print(f"No folder the key can see matches {' '.join(args.folder)}.")
        return

    rows = []
    for folder in folders:
        try:
            regions = fetch_map_regions(folder["folderId"], auth)
        except Exception as error:
            message = str(error).strip().splitlines()
            print(f"Could not list folder {folder['folderId']} ({folder.get('name')}): "
                  f"{message[0] if message else type(error).__name__}")
            continue

        for region in regions:
            rows.append({
                "cd": (folder.get("name") or "") if CD_FROM_FOLDER_NAME else "",
                "folder_id": folder["folderId"],
                "map_region_id": region.get("mapRegionId"),
                "map_region_name": (region.get("name") or "").strip(),
                "report_format": DEFAULT_REPORT_FORMAT,
                "script": DEFAULT_SCRIPT,
                "minivan_campaign": DEFAULT_MINIVAN_CAMPAIGN,
                "folder_name": folder.get("name") or "",
            })
        print(f"{len(regions):5} map regions  {folder['folderId']}  {folder.get('name')}")

    # Headers come from SHEET_COLUMNS, so a renamed column in mapped_packets.py carries over.
    headers = [SHEET_COLUMNS[field] for field in FIELDS] + EXTRA
    with open(OUTPUT, "w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(headers)
        for row in rows:
            writer.writerow([row[field] for field in FIELDS + EXTRA])

    print(f"\nSaved {len(rows)} map regions from {len(folders)} folders to {OUTPUT}")


if __name__ == "__main__":
    main()