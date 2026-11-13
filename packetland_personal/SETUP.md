# Packet flows: refresh, print, download, list numbers

Three flows, run in this order. Each reads the Packet Tracker sheet directly.

1. `van_refresh_packets`: refreshes every region on the sheet through the VAN API and waits for VAN to finish.
2. `van_print_packets`: drives VoteBuilder's print wizard for each region, which also pushes it to MiniVAN.
3. `van_download_packets`: downloads each region's newest PDF to `Packets / <cd> / <Saturday> / <region>.pdf` in Drive.

A fourth flow, `van_list_numbers`, generates MiniVAN list numbers through My Turfs without printing PDFs. Two helper scripts in the repo root, `list_regions.py` and `list_drive_folders.py`, collect the IDs the sheet and settings need.

## Install

Needs Python 3.10 or newer and Google Chrome. Selenium 4.6+ downloads the matching ChromeDriver itself.

```bash
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

Run everything from the repo root with `python -m`, using dots in place of slashes and leaving off `.py`.

## Fill in

| File | Setting | What goes there |
| --- | --- | --- |
| `flows/packetland/mapped_packets.py` | `SERVICE_ACCOUNT_SECRET` | The service account JSON key |
| | `VAN_SECRET` | `{"NY": {"app_name": "...", "app_key": "..."}}` |
| | `PACKET_TRACKER_SHEET_ID`, `PACKET_TRACKER_TAB` | The sheet's ID from its URL (between `/d/` and `/edit`), and the tab name |
| | `SHEET_COLUMNS` | Your header names, if they differ from the defaults |
| | `PACKETS_DRIVE_FOLDER_ID` | The Drive folder the PDFs go in. It must be in a Shared Drive |
| | `TEST_ROWS` | Your throwaway test region |
| `core_utilities/van_browser_utilities.py` | `ACTIONID_SECRET`, `DEFAULT_ACTIONID_USER` | `{"bot@...": {"password": "...", "totp_secret": "..."}}`, and that email |
| `flows/van_automation/target_slots.py` | `COMMITTEE` | Your committee as VAN's switcher shows it, or `None` if the bot has one committee |

Each secret setting can be a local JSON file or a Secret Manager name. `get_secret` reads a file unless the name starts with `projects/`. By default the files are `packet-bot-key.json`, `van.json` and `actionid.json` in `flows/packetland/resources/`. That folder holds logins and keys, so `.gitignore` keeps it out of git. If you use Secret Manager instead, also run `gcloud auth application-default login`.

Share the sheet and the Drive folder with the service account's `client_email` (Content manager on the Drive folder). Share every turf folder with the VAN API user.

## The sheet

One row per region, with a header row. Columns: `cd`, `folder_id`, `map_region_id`, `map_region_name`, `report_format`, `script`, and optionally `minivan_campaign`.

- `cd` is whatever you group by. It names the Drive folder and is what `--only` filters on.
- Report format, script and campaign must match VAN's dropdown labels exactly.
- With a campaign, the script has to be the campaign's own default.
- On the Queue tab, pick what to run in column A: a staging folder, a VAN folder name, or a single map region ID. To run only part of a folder, fill `rank_from` and `rank_to` on the same row.

Every run checks each row before doing anything. A row with a blank required field, a non-numeric ID, a repeat of an earlier row, or a region VAN doesn't have under that ID and name is skipped with the reason. The rest still run, and the skipped rows are listed again at the end.

## Helper scripts

Run these from the repo root with plain `python`.

- **`list_regions.py`** lists every map region the VAN API key can see and writes `packet_tracker_rows.csv`, laid out like the Packet Tracker. `--folder` narrows it to folders whose name contains the text, or to a folder ID. A folder that isn't shared with the API user won't appear.
  ```bash
  python list_regions.py
  python list_regions.py --folder <folder name or ID>
  ```
- **`list_drive_folders.py`** lists the folders inside `PACKETS_DRIVE_FOLDER_ID` and writes `drive_folders.csv`. Use it to check each staging location's Drive folder. It warns if the folder isn't in a Shared Drive.
  ```bash
  python list_drive_folders.py
  ```

Both CSVs contain internal IDs, and `.gitignore` keeps them out of git.

## Test, in order

```bash
python -m flows.packetland.van_refresh_packets.van_refresh_packets --test-district
python -m flows.packetland.van_print_packets.van_print_packets --test-district --stored-login            # dry run
python -m flows.packetland.van_print_packets.van_print_packets --test-district --stored-login --commit
python -m flows.packetland.van_download_packets.van_download_packets --test-district --stored-login --dry-run
python -m flows.packetland.van_download_packets.van_download_packets --test-district --stored-login
```

A 403 on refresh means the API key lacks the extended permission.

## Run

```bash
python -m flows.packetland.van_refresh_packets.van_refresh_packets && \
python -m flows.packetland.van_print_packets.van_print_packets --commit --stored-login --headless && \
python -m flows.packetland.van_download_packets.van_download_packets --stored-login --headless
```

Every print run gives each region a new list number, so rerun single regions with `--only` and `--region`. If print fails, download doesn't run; it can be rerun on its own. Download looks for PDFs printed in the last day; add `--since YYYY-MM-DD` for older ones. Screenshots of failures land in `van_automation_artifacts/`, which should stay out of git.

## List numbers without printing

`van_list_numbers` runs every list on the Queue through My Turfs: it searches for the list, ticks it, and picks Generate List Number from Quick Actions. Regions that share a name are handled together as one list. Without `--commit` it fills in each list's page and clicks Exit, so nothing is generated.

```bash
python -m flows.packetland.van_list_numbers.van_list_numbers --stored-login             # dry run
python -m flows.packetland.van_list_numbers.van_list_numbers --stored-login --commit
```

- **Already numbered lists are skipped.** A list whose turfs all show a list number in My Turfs is skipped. `--again` generates them anyway.
- **`--retry FILE`** reruns only the lists that failed, from a run's summary or its `failed_lists.txt`. A run with failures prints the exact command.
- **`--skip FILE`** leaves out the lists an earlier run generated, from that run's saved terminal output or its summary.
- **Filters** work as for print: `--only "<staging location>"`, `--region <name> ...`, `--headless`.
- The last line says how many map regions got list numbers, or `NOTHING WAS GENERATED` on a dry run. Pages for failed lists are saved in `van_automation_artifacts/`.

`capture_my_turfs.py` does the same steps for a single list, and `van_list_numbers` reuses them:

```bash
python -m flows.packetland.van_list_numbers.capture_my_turfs --stored-login --region "<map region name>"
python -m flows.packetland.van_list_numbers.capture_my_turfs --stored-login --region "<map region name>" --commit
```

## Speed

`PACE` in `flows/van_automation/van_ui.py` sets the pause between actions in the print wizard, and `LIST_PACE_SECONDS` in `capture_my_turfs.py` sets the pace for list numbers. VAN can suspend an account for automated access that runs too fast, and no setting is guaranteed safe. Run large jobs in smaller batches, and check with your VAN admin that automated access is allowed for the account.