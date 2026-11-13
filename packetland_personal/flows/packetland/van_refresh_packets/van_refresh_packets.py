"""
Refreshes the turf packets organizers asked for on the Packet Tracker sheet.

Queues every mapped region's folder for a refresh over the API and waits for VAN to
finish. The print flow, a separate Batch job, reads back what this one did from VAN
rather than being handed anything in memory.
"""

import argparse
import time
from datetime import datetime, timedelta, timezone

from core_utilities.gcp_utilities import get_secret
from core_utilities.van_utilities import (
    fetch_map_regions,
    get_van_auth_for_state,
    refresh_folder_map_regions,
)
from flows.packetland.mapped_packets import (
    TEST_STATE,
    VAN_DATABASE,
    VAN_SECRET,
    finish,
    load_mapped_packets,
    parse_van_timestamp,
    region_key,
    row_context,
)
from flows.van_automation.target_slots import by_state

### -------------------------------------------------------------------------------------------------------------------------------------------- ###
## VAN REFRESH PACKETS FLOW --------------------------------------------------------------------------------------------------------------------- ###
### -------------------------------------------------------------------------------------------------------------------------------------------- ###

# VAN queues refreshes. Long enough to wait out a busy queue, short enough that a
# stuck one fails the run rather than holding a Batch job all morning.
REFRESH_WAIT_SECONDS = 1200
REFRESH_POLL_SECONDS = 30

### -------------------------------------------------------------------------------------------------------------------------------------------- ###

def refresh_regions(rows: list, van_keys: dict, pipeline_errors: list) -> list:
    """Queues every folder with a mapped region for a refresh, one call per folder.
    Returns the rows that were queued."""
    queued = []
    folders = {}

    for row in rows:
        folders.setdefault((row["state"], row["folder_id"]), []).append(row)

    for (state, folder_id), folder_rows in folders.items():
        if state.upper() not in van_keys:
            for row in folder_rows:
                pipeline_errors.append({
                    "stage": "Refresh",
                    "context": row_context(row),
                    "error": f"No VAN API key configured for {state}, so its turf cannot be refreshed.",
                })
            continue

        try:
            van_auth = get_van_auth_for_state(van_keys, state, VAN_DATABASE)
            refresh_folder_map_regions(folder_id, van_auth)
        except Exception as refresh_error:
            for row in folder_rows:
                pipeline_errors.append({
                    "stage": "Refresh",
                    "context": row_context(row),
                    "error": f"VAN refused the refresh: {refresh_error}",
                })
            continue

        queued.extend(folder_rows)

    return queued

### -------------------------------------------------------------------------------------------------------------------------------------------- ###

def wait_for_refreshes(queued: list, run_started_at: datetime, van_keys: dict,
                       pipeline_errors: list) -> list:
    """Polls until every queued region carries a dateRefreshed from this run, and returns
    the rows that do. A region still stale at the deadline is an error and is not printed."""
    if not queued:
        return []

    waiting = {region_key(row): row for row in queued}
    last_seen = {}
    refreshed = []
    deadline = time.monotonic() + REFRESH_WAIT_SECONDS

    print(f"Waiting on {len(waiting)} map region refreshes.")

    while waiting and time.monotonic() < deadline:
        time.sleep(REFRESH_POLL_SECONDS)

        # One fetch per folder still waiting, then check only that folder's regions.
        by_folder = {}
        for key in waiting:
            by_folder.setdefault(key[:2], []).append(key)

        for (state, folder_id), keys in by_folder.items():
            try:
                van_auth = get_van_auth_for_state(van_keys, state, VAN_DATABASE)
                current = {str(region.get("mapRegionId")): region.get("dateRefreshed")
                           for region in fetch_map_regions(folder_id, van_auth)}
            except Exception as fetch_error:
                print(f"Could not poll folder {folder_id} in {state}: {fetch_error}")
                continue

            for key in keys:
                last_seen[key] = current.get(key[2])
                refreshed_at = parse_van_timestamp(last_seen[key])
                if refreshed_at is not None and refreshed_at >= run_started_at:
                    refreshed.append(waiting.pop(key))

        if waiting:
            print(f"{len(waiting)} still refreshing.")

    # The stamp VAN last showed, so a stall reads as a slow queue or a wrong clock.
    for key, row in waiting.items():
        pipeline_errors.append({
            "stage": "Refresh",
            "context": row_context(row),
            "error": (f"Still had not refreshed {REFRESH_WAIT_SECONDS} seconds after being "
                      f"queued at {run_started_at:%Y-%m-%dT%H:%M}Z; VAN last showed "
                      f"dateRefreshed {last_seen.get(key)}."),
        })

    print(f"{len(refreshed)} map regions refreshed.")
    return refreshed

### -------------------------------------------------------------------------------------------------------------------------------------------- ###

def refresh_packets(only: list = None, region: list = None,
                    test_district: bool = False) -> list:
    """
    Queues every mapped region's folder for a refresh and waits for it.

    API only, no browser. Runs on its own so van_print_packets starts against turf that
    is already cut fresh, and so a VAN queue that is having a bad morning fails here
    rather than halfway through a browser session.
    """
    pipeline_errors = []
    # A minute early: VAN stamps dateRefreshed to the minute.
    run_started_at = datetime.now(timezone.utc) - timedelta(minutes = 1)
    rows = load_mapped_packets(only, region, test_district)

    print(f"\nRefreshing {len(rows)} map regions across {len(by_state(rows))} states.")

    van_keys = get_secret(VAN_SECRET)
    queued = refresh_regions(rows, van_keys, pipeline_errors)
    refreshed = wait_for_refreshes(queued, run_started_at, van_keys, pipeline_errors)

    reports = [{"context": row_context(row), "action": "refreshed"} for row in refreshed]

    return finish("refresh", reports, pipeline_errors)

### -------------------------------------------------------------------------------------------------------------------------------------------- ###

def main():
    parser = argparse.ArgumentParser(
        description="Refresh the turf packets on the Packet Tracker sheet"
    )
    parser.add_argument("--only", nargs="+", metavar="CD",
                        help="Only these districts, e.g. --only ST-01 ST-02.")
    parser.add_argument("--region", nargs="+", metavar="NAME",
                        help="Only these map regions, by name.")
    parser.add_argument("--test-district", action="store_true",
                        help=f"Refresh the {TEST_STATE} test region instead of reading the sheet.")
    args = parser.parse_args()

    refresh_packets(args.only, args.region, args.test_district)


if __name__ == "__main__":
    main()
