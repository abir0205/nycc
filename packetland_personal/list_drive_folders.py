"""
Lists the folders inside PACKETS_DRIVE_FOLDER_ID, the staging-location folders the PDFs
should land in, and saves their names to drive_folders.csv next to this file.

Run it from the packetland_2 folder:

    python list_drive_folders.py

It only reads Drive. It also proves the service account can see the folder: a 404 means
PACKETS_DRIVE_FOLDER_ID is wrong or the folder is not shared with the service account.
"""

import csv
from pathlib import Path

from core_utilities.gsheet_utilities import auth_to_drive
from flows.packetland.mapped_packets import PACKETS_DRIVE_FOLDER_ID, SERVICE_ACCOUNT_SECRET

OUTPUT = Path(__file__).resolve().parent / "drive_folders.csv"


def main():
    drive = auth_to_drive(SERVICE_ACCOUNT_SECRET)

    parent = drive.files().get(fileId=PACKETS_DRIVE_FOLDER_ID, fields="name, driveId",
                               supportsAllDrives=True).execute()
    print(f"Parent folder: {parent['name']}")
    if not parent.get("driveId"):
        print("Warning: this folder is in someone's My Drive, not a Shared Drive. "
              "The service account cannot upload there; move it into a Shared Drive.")

    folders, page = [], None
    while True:
        result = drive.files().list(
            q=(f"'{PACKETS_DRIVE_FOLDER_ID}' in parents and trashed = false "
               "and mimeType = 'application/vnd.google-apps.folder'"),
            fields="nextPageToken, files(id, name)", pageSize=1000, pageToken=page,
            supportsAllDrives=True, includeItemsFromAllDrives=True,
        ).execute()
        folders += result.get("files", [])
        page = result.get("nextPageToken")
        if not page:
            break

    folders.sort(key=lambda f: f["name"])
    with open(OUTPUT, "w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(["drive_folder_name", "drive_folder_id"])
        for folder in folders:
            writer.writerow([folder["name"], folder["id"]])

    print(f"Saved {len(folders)} folders to {OUTPUT}")


if __name__ == "__main__":
    main()
