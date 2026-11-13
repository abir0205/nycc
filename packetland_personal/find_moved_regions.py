"""
Finds the Packet Tracker rows whose map region VAN no longer has under that ID, says what
happened to each one, and writes a corrected copy of All Regions' first four columns.

The print skips a row whose folder has no region with the sheet's ID ("folder ... has no
map region ..."). That happens when turf is recut or cleaned up in VAN after the sheet was
built: the region keeps its name but gets a new ID, moves to another folder, or is gone.
This looks every such row up by name across every folder the API key can see.

Run it from the packetland_2 folder. It only reads, from VAN and from the sheet:

    python find_moved_regions.py

It writes two files next to this one:
    region_fixes.csv        every Packet Tracker row that does not match VAN, and what VAN has now
    all_regions_A_to_D.csv  All Regions' folder_id, folder_name, map_region_id and map_region_name,
                            with each stale region switched to the one VAN has now, where there is
                            exactly one. Import it over All Regions A1 (File > Import > Upload >
                            "Replace data at selected cell") and the Queue and Packet Tracker pick
                            the new IDs up.
"""

import csv
from collections import defaultdict
from pathlib import Path

from core_utilities.gcp_utilities import get_secret
from core_utilities.gsheet_utilities import read_sheet_values
from core_utilities.van_utilities import fetch_folders, fetch_map_regions, get_van_auth_for_state
from flows.packetland.mapped_packets import (
    PACKET_TRACKER_SHEET_ID,
    SERVICE_ACCOUNT_SECRET,
    STATE,
    VAN_DATABASE,
    VAN_SECRET,
    clean_cell,
    read_packet_tracker,
)

HERE = Path(__file__).resolve().parent
FIXES = HERE / "region_fixes.csv"
ALL_REGIONS_OUT = HERE / "all_regions_A_to_D.csv"
ALL_REGIONS_RANGE = "'All Regions'!A:D"


def van_regions(auth) -> tuple:
    """Every region the key can see, as {(folder_id, region_id): name} and folder names."""
    folders = {str(f.get("folderId")): (f.get("name") or "").strip() for f in fetch_folders(auth)}
    regions = {}
    unreadable = []
    for number, folder_id in enumerate(folders, start=1):
        print(f"\rReading VAN folders: {number}/{len(folders)}", end="", flush=True)
        try:
            for region in fetch_map_regions(folder_id, auth):
                regions[(folder_id, str(region.get("mapRegionId")))] = (region.get("name") or "").strip()
        except Exception as error:
            message = str(error).strip().splitlines()
            unreadable.append(f"{folder_id} ({folders[folder_id]}): {message[0] if message else type(error).__name__}")
    print()
    for line in unreadable:
        print(f"Could not list folder {line}")
    return regions, folders


def what_happened(folder_id: str, region_id: str, name: str, regions: dict, by_name: dict) -> tuple:
    """
    (status, [(folder_id, region_id), ...]) for one sheet region. Status is ok, renamed,
    new id, moved, several or gone.
    """
    van_name = regions.get((folder_id, region_id))
    if van_name == name:
        return "ok", [(folder_id, region_id)]
    if van_name is not None:
        return "renamed", [(folder_id, region_id)]

    same_name = by_name.get(name, [])
    in_folder = [key for key in same_name if key[0] == folder_id]
    if len(in_folder) == 1:
        return "new id", in_folder
    if len(in_folder) > 1:
        return "several", in_folder
    if len(same_name) == 1:
        return "moved", same_name
    if len(same_name) > 1:
        return "several", same_name
    return "gone", []


def main():
    auth = get_van_auth_for_state(get_secret(VAN_SECRET), STATE, VAN_DATABASE)
    regions, folders = van_regions(auth)
    by_name = defaultdict(list)
    for key, name in regions.items():
        by_name[name].append(key)

    # The Packet Tracker rows the print reads.
    stale = []
    for row in read_packet_tracker():
        status, found = what_happened(row["folder_id"], row["map_region_id"], row["map_region_name"],
                                      regions, by_name)
        if status != "ok":
            stale.append((row, status, found))

    with open(FIXES, "w", newline="") as handle:
        out = csv.writer(handle)
        out.writerow(["sheet_row", "cd", "map_region_name", "sheet_folder_id", "sheet_map_region_id",
                      "what_happened", "van_folder_id", "van_folder_name", "van_map_region_id", "van_name"])
        for row, status, found in stale:
            for folder_id, region_id in found or [("", "")]:
                out.writerow([row["sheet_row"], row["cd"], row["map_region_name"], row["folder_id"],
                              row["map_region_id"], status, folder_id, folders.get(folder_id, ""),
                              region_id, regions.get((folder_id, region_id), "")])

    counts = defaultdict(int)
    for _, status, _ in stale:
        counts[status] += 1
    meaning = {
        "new id": "same name, same folder, new ID in VAN (recut): fixed in the file below",
        "moved": "same name, now in another folder: fixed in the file below",
        "renamed": "the ID is still there, under another name: fix the name on the sheet",
        "several": "VAN has more than one region by that name: pick the right ID by hand",
        "gone": "no region by that name in any folder: it was deleted, so recut it or take it off the Queue",
    }
    print(f"\n{len(stale)} Packet Tracker rows do not match VAN.")
    for status in meaning:
        if counts[status]:
            print(f"  {counts[status]:4}  {meaning[status]}")
    print(f"Details: {FIXES}")

    # All Regions, so every stale region is fixed, not only this round's.
    values = read_sheet_values(PACKET_TRACKER_SHEET_ID, ALL_REGIONS_RANGE, SERVICE_ACCOUNT_SECRET)
    header, rows = values[0], values[1:]
    on_sheet = {(clean_cell(r[0]) if len(r) > 0 else "", clean_cell(r[2]) if len(r) > 2 else "")
                for r in rows}

    changed = 0
    held_back = 0
    with open(ALL_REGIONS_OUT, "w", newline="") as handle:
        out = csv.writer(handle)
        out.writerow(header[:4])
        for r in rows:
            cells = [clean_cell(r[i]) if i < len(r) else "" for i in range(4)]
            folder_id, folder_name, region_id, name = cells
            if folder_id and region_id and name:
                status, found = what_happened(folder_id, region_id, name, regions, by_name)
                if status in ("new id", "moved"):
                    new_folder, new_id = found[0]
                    if (new_folder, new_id) in on_sheet:
                        # The live region already has its own row, so changing this one
                        # would list it twice. The stale row can just be deleted.
                        held_back += 1
                    else:
                        cells = [new_folder, folders.get(new_folder, folder_name), new_id, name]
                        changed += 1
            out.writerow(cells)

    print(f"\nAll Regions: {changed} of {len(rows)} rows switched to the region VAN has now.")
    if held_back:
        print(f"  {held_back} stale rows left as they are, because the live region already has its "
              "own row on All Regions. Those stale rows can be deleted.")
    print(f"Wrote {ALL_REGIONS_OUT}. Import it over All Regions A1 with File > Import > Upload > "
          "'Replace data at selected cell'. Columns E onward are not touched.")


if __name__ == "__main__":
    main()
