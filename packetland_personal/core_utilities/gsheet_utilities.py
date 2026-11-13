import ssl
import time

import google_auth_httplib2
import httplib2
from googleapiclient.discovery import build
from googleapiclient.http import MediaInMemoryUpload, build_http
from core_utilities.gcp_utilities import get_credentials, get_secret

# Drive returns 403 userRateLimitExceeded under load. googleapiclient treats that
# as retryable, but only backs off when num_retries is set.
DRIVE_NUM_RETRIES = 6

# How long a Drive call may wait on the network before it gives up, in seconds. The
# library's own default is 60, which a big packet PDF on a slow connection can outrun:
# that is "The read operation timed out".
DRIVE_TIMEOUT_SECONDS = 300

# An upload goes up in pieces this size, so each request is short. Without it the whole
# PDF goes in one request, which is what the timeout above cuts off. Drive wants a
# multiple of 256 KB.
UPLOAD_CHUNK_BYTES = 5 * 1024 * 1024

# googleapiclient retries an upload piece that Drive answers with an error, but not one
# the network drops, so the whole upload is tried this many times before it fails.
UPLOAD_ATTEMPTS = 3
UPLOAD_RETRY_PAUSE_SECONDS = 20
NETWORK_ERRORS = (TimeoutError, ConnectionError, ssl.SSLError, httplib2.HttpLib2Error)


### -------------------------------------------------------------------------------------------------------------------------------------------- ###

def auth_to_drive(credential_file: str):
    """Authenticates to Google Drive using a service account JSON key file."""

    creds = get_credentials(get_secret(credential_file))

    # build_http keeps the handling Drive's resumable uploads need (308 is not a
    # redirect); only its timeout changes.
    http = build_http()
    http.timeout = DRIVE_TIMEOUT_SECONDS
    drive_service = build('drive', 'v3', http=google_auth_httplib2.AuthorizedHttp(creds, http=http))

    return drive_service

### -------------------------------------------------------------------------------------------------------------------------------------------- ###

def auth_to_sheets(credential_file: str):
    """Authenticates to Google Sheets using a service account JSON key file."""

    creds = get_credentials(get_secret(credential_file))

    return build('sheets', 'v4', credentials=creds)

### -------------------------------------------------------------------------------------------------------------------------------------------- ###

def read_sheet_values(spreadsheet_id: str, range_name: str, credential_file: str) -> list:
    """
    Returns every row in a range as a list of cell values, header row included.

    Unformatted, so a number comes back as a number rather than as the sheet displays
    it: an id formatted as 1,234,567 reads as 1234567. Trailing blank cells are left off
    each row, the way the API returns them.
    """
    sheets_service = auth_to_sheets(credential_file)
    result = sheets_service.spreadsheets().values().get(
        spreadsheetId=spreadsheet_id,
        range=range_name,
        valueRenderOption='UNFORMATTED_VALUE',
    ).execute(num_retries=DRIVE_NUM_RETRIES)

    return result.get('values', [])

### -------------------------------------------------------------------------------------------------------------------------------------------- ###

def drive_query_literal(value: str) -> str:
    """Escapes a value for a quoted string in Drive's query language, so O'Neil works."""

    return value.replace("\\", "\\\\").replace("'", "\\'")

### -------------------------------------------------------------------------------------------------------------------------------------------- ###

def find_or_create_folder(folder_name: str, parent_folder_id: str, drive_service) -> str:
    """Finds a Drive folder by name within a specified parent folder. If it doesn't exist, creates it."""

    folder_query = (
        f"name = '{drive_query_literal(folder_name)}' and '{parent_folder_id}' in parents "
        "and mimeType = 'application/vnd.google-apps.folder' and trashed = false"
    )
    folder_res = drive_service.files().list(
        q=folder_query,
        fields='files(id)',
        supportsAllDrives=True,
        includeItemsFromAllDrives=True,
    ).execute(num_retries=DRIVE_NUM_RETRIES)
    folders = folder_res.get('files', [])

    if folders:
        return folders[0]['id']

    folder_metadata = {
        'name': folder_name,
        'mimeType': 'application/vnd.google-apps.folder',
        'parents': [parent_folder_id],
    }
    created_folder = drive_service.files().create(
        body=folder_metadata, fields='id', supportsAllDrives=True,
    ).execute(num_retries=DRIVE_NUM_RETRIES)

    return created_folder.get('id')

### -------------------------------------------------------------------------------------------------------------------------------------------- ###

def find_drive_file(file_name: str, parent_folder_id: str, drive_service) -> dict | None:
    """Returns {id, size} for the file with this name in a Drive folder, or None."""

    file_query = (
        f"name = '{drive_query_literal(file_name)}' and '{parent_folder_id}' in parents "
        "and trashed = false"
    )
    file_res = drive_service.files().list(
        q=file_query,
        fields='files(id, size)',
        supportsAllDrives=True,
        includeItemsFromAllDrives=True,
    ).execute(num_retries=DRIVE_NUM_RETRIES)
    files = file_res.get('files', [])

    return files[0] if files else None

### -------------------------------------------------------------------------------------------------------------------------------------------- ###

def upload_file_to_drive_folder(file_name: str, file_bytes: bytes, parent_folder_id: str, drive_service,
                                mimetype: str) -> str:
    """Uploads a file to a Drive folder. If a file with the same name already exists in the folder, overwrites its contents in place; otherwise creates a new file. Returns the file ID."""

    for attempt in range(1, UPLOAD_ATTEMPTS + 1):
        # Resumable, in UPLOAD_CHUNK_BYTES pieces, so a big file is many short requests.
        media = MediaInMemoryUpload(file_bytes, mimetype=mimetype, chunksize=UPLOAD_CHUNK_BYTES,
                                    resumable=True)
        try:
            # Looked up on every attempt: a create the network cut off may still have made
            # the file, and this overwrites it rather than making a second one.
            existing = find_drive_file(file_name, parent_folder_id, drive_service)

            # If it exists, overwrite its contents; otherwise create it in the folder
            if existing:
                file_id = existing['id']
                drive_service.files().update(
                    fileId=file_id, media_body=media, supportsAllDrives=True
                ).execute(num_retries=DRIVE_NUM_RETRIES)
            else:
                file_metadata = {'name': file_name, 'parents': [parent_folder_id]}
                created_file = drive_service.files().create(
                    body=file_metadata, media_body=media, fields='id', supportsAllDrives=True
                ).execute(num_retries=DRIVE_NUM_RETRIES)
                file_id = created_file.get('id')
            return file_id

        except NETWORK_ERRORS as network_error:
            if attempt == UPLOAD_ATTEMPTS:
                raise RuntimeError(
                    f"Uploading {file_name} ({len(file_bytes) / 1024 / 1024:.1f} MB) to Drive failed "
                    f"{UPLOAD_ATTEMPTS} times on the network: {network_error}"
                ) from network_error
            print(f"Drive upload of {file_name} hit a network error ({network_error}); "
                  f"trying again in {UPLOAD_RETRY_PAUSE_SECONDS * attempt}s.", flush=True)
            time.sleep(UPLOAD_RETRY_PAUSE_SECONDS * attempt)

### -------------------------------------------------------------------------------------------------------------------------------------------- ###

