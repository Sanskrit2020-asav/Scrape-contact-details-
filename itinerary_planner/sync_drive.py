"""Sync prepared itineraries from a Google Drive folder into itineraries/.

This runs wherever you have your Google credentials (typically your Mac).
It does NOT run inside the cloud session — that container has no access to
your Drive. Run it locally, commit the synced files, and push.

Auth — two supported modes:

  1. Service account (best for unattended/local sync of a folder you own or
     that is shared with the service account's email):
       export GDRIVE_SERVICE_ACCOUNT=/path/to/service-account.json

  2. OAuth client (Desktop app credentials, opens a browser once):
       export GDRIVE_OAUTH_CLIENT=/path/to/oauth_client.json
     A token is cached at .gdrive_token.json so later runs are silent.

Folder — pass the folder ID or its share URL:
       python -m itinerary_planner.sync_drive --folder <ID-or-URL>
   or set GDRIVE_FOLDER_ID.

Only PDF, DOCX, TXT and Markdown files are pulled. Google Docs are exported
to .docx automatically. Files unchanged since the last sync are skipped.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path

from .index import ITINERARIES_DIR

SCOPES = ["https://www.googleapis.com/auth/drive.readonly"]
TOKEN_FILE = Path(__file__).resolve().parent.parent / ".gdrive_token.json"
SYNC_STATE = Path(__file__).resolve().parent.parent / "output" / "gdrive_sync.json"

# Drive mime types we care about -> local extension (None = native binary).
EXPORTABLE = {"application/vnd.google-apps.document": ".docx"}
KEEP_MIME = {
    "application/pdf": ".pdf",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document": ".docx",
    "text/plain": ".txt",
    "text/markdown": ".md",
}
FOLDER_MIME = "application/vnd.google-apps.folder"
DOC_EXPORT_MIME = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"

# Sensitive / non-itinerary files: never sync these (privacy + matching noise).
EXCLUDE_KEYWORDS = (
    "passport",
    "invoice",
    "scanned_",
    "scanned ",
    "education consultancy",
    "porter guide assistance",
    "north nepal travel and trek",
    "namaste and good evening",
)
EXCLUDE_EXACT = {
    "subject.docx",
    "passport.pdf",
    "passport no..pdf",
    "permit upper mustang.pdf",
}


def is_excluded(name: str) -> bool:
    low = name.lower()
    if low in EXCLUDE_EXACT:
        return True
    return any(k in low for k in EXCLUDE_KEYWORDS)


def _folder_id(value: str) -> str:
    """Accept a raw ID or a Drive folder URL."""
    m = re.search(r"/folders/([A-Za-z0-9_-]+)", value)
    if m:
        return m.group(1)
    m = re.search(r"[?&]id=([A-Za-z0-9_-]+)", value)
    if m:
        return m.group(1)
    return value.strip()


def _build_service():
    from googleapiclient.discovery import build

    sa = os.environ.get("GDRIVE_SERVICE_ACCOUNT")
    if sa:
        from google.oauth2 import service_account

        creds = service_account.Credentials.from_service_account_file(sa, scopes=SCOPES)
        return build("drive", "v3", credentials=creds, cache_discovery=False)

    client = os.environ.get("GDRIVE_OAUTH_CLIENT")
    if client:
        from google.auth.transport.requests import Request
        from google.oauth2.credentials import Credentials
        from google_auth_oauthlib.flow import InstalledAppFlow

        creds = None
        if TOKEN_FILE.exists():
            creds = Credentials.from_authorized_user_file(str(TOKEN_FILE), SCOPES)
        if not creds or not creds.valid:
            if creds and creds.expired and creds.refresh_token:
                creds.refresh(Request())
            else:
                flow = InstalledAppFlow.from_client_secrets_file(client, SCOPES)
                creds = flow.run_local_server(port=0)
            TOKEN_FILE.write_text(creds.to_json())
        return build("drive", "v3", credentials=creds, cache_discovery=False)

    sys.exit(
        "No Google credentials found. Set GDRIVE_SERVICE_ACCOUNT or "
        "GDRIVE_OAUTH_CLIENT (see module docstring)."
    )


def _list_folder(service, folder_id: str, recurse: bool) -> list[dict]:
    items: list[dict] = []
    stack = [folder_id]
    while stack:
        current = stack.pop()
        page_token = None
        while True:
            resp = (
                service.files()
                .list(
                    q=f"'{current}' in parents and trashed = false",
                    fields="nextPageToken, files(id, name, mimeType, modifiedTime)",
                    pageSize=200,
                    pageToken=page_token,
                    supportsAllDrives=True,
                    includeItemsFromAllDrives=True,
                )
                .execute()
            )
            for f in resp.get("files", []):
                if f["mimeType"] == FOLDER_MIME:
                    if recurse:
                        stack.append(f["id"])
                else:
                    items.append(f)
            page_token = resp.get("nextPageToken")
            if not page_token:
                break
    return items


def _safe_name(name: str, ext: str) -> str:
    # Drive names can contain "/", so do not treat the name as a path.
    base = name
    for known in (".pdf", ".docx", ".txt", ".md", ".gdoc"):
        if base.lower().endswith(known):
            base = base[: -len(known)]
            break
    stem = re.sub(r"[^\w.\- ]+", "_", base).strip() or "itinerary"
    return f"{stem}{ext}"


def sync(folder: str, recurse: bool = True) -> int:
    from googleapiclient.errors import HttpError
    from googleapiclient.http import MediaIoBaseDownload

    folder_id = _folder_id(folder)
    service = _build_service()
    ITINERARIES_DIR.mkdir(parents=True, exist_ok=True)

    # state maps Drive file id -> {"mtime": <modifiedTime>, "name": <local filename>}.
    raw_state: dict = {}
    if SYNC_STATE.exists():
        try:
            raw_state = json.loads(SYNC_STATE.read_text())
        except Exception:
            raw_state = {}
    state: dict[str, dict] = {}
    for fid, val in raw_state.items():
        state[fid] = val if isinstance(val, dict) else {"mtime": val, "name": None}

    taken = {v["name"] for v in state.values() if v.get("name")}

    def unique_name(file_id: str, drive_name: str, ext: str) -> str:
        prev = state.get(file_id, {}).get("name")
        if prev:
            return prev
        base = _safe_name(drive_name, ext)
        if base not in taken:
            taken.add(base)
            return base
        stem, suffix = base[: -len(ext)], ext
        i = 2
        while f"{stem} ({i}){suffix}" in taken:
            i += 1
        chosen = f"{stem} ({i}){suffix}"
        taken.add(chosen)
        return chosen

    files = _list_folder(service, folder_id, recurse)
    pulled = skipped = excluded = 0
    failed: list[tuple[str, str]] = []
    for f in files:
        if is_excluded(f.get("name", "")):
            excluded += 1
            continue
        mime = f["mimeType"]
        if mime in EXPORTABLE:
            ext = EXPORTABLE[mime]
        elif mime in KEEP_MIME:
            ext = KEEP_MIME[mime]
        else:
            continue

        local_name = unique_name(f["id"], f["name"], ext)
        target = ITINERARIES_DIR / local_name

        if (
            state.get(f["id"], {}).get("mtime") == f["modifiedTime"]
            and target.exists()
        ):
            skipped += 1
            continue

        def _download(request, dest: Path) -> None:
            with open(dest, "wb") as fh:
                downloader = MediaIoBaseDownload(fh, request)
                done = False
                while not done:
                    _, done = downloader.next_chunk()

        try:
            if mime in EXPORTABLE:
                try:
                    _download(
                        service.files().export_media(
                            fileId=f["id"], mimeType=DOC_EXPORT_MIME
                        ),
                        target,
                    )
                except HttpError as exc:
                    # Google Docs over the .docx export limit: fall back to PDF.
                    if "exportSizeLimitExceeded" not in str(exc):
                        raise
                    target.unlink(missing_ok=True)
                    target = target.with_suffix(".pdf")
                    local_name = target.name
                    _download(
                        service.files().export_media(
                            fileId=f["id"], mimeType="application/pdf"
                        ),
                        target,
                    )
                    print("  (large doc exported as PDF instead of DOCX)")
            else:
                _download(service.files().get_media(fileId=f["id"]), target)
        except Exception as exc:  # noqa: BLE001 - one bad file must not abort the sync
            target.unlink(missing_ok=True)
            failed.append((f.get("name", f["id"]), str(exc).split("\n")[0]))
            print(f"  SKIPPED {f.get('name', f['id'])}: {str(exc).splitlines()[0]}")
            continue

        state[f["id"]] = {"mtime": f["modifiedTime"], "name": local_name}
        pulled += 1
        print(f"  pulled  {target.name}")

    SYNC_STATE.parent.mkdir(parents=True, exist_ok=True)
    SYNC_STATE.write_text(json.dumps(state, indent=2))
    print(
        f"\nDrive sync complete: {pulled} pulled, {skipped} unchanged, "
        f"{excluded} excluded (sensitive/non-itinerary), {len(failed)} failed."
    )
    if failed:
        print("Failed files (left in Drive, not synced):")
        for name, why in failed:
            print(f"  - {name}: {why}")
    print("Now run the planner and click Re-index (or commit + push the files).")
    return pulled


def main() -> None:
    parser = argparse.ArgumentParser(description="Sync itineraries from Google Drive.")
    parser.add_argument(
        "--folder",
        default=os.environ.get("GDRIVE_FOLDER_ID", ""),
        help="Drive folder ID or share URL (or set GDRIVE_FOLDER_ID).",
    )
    parser.add_argument(
        "--no-recurse", action="store_true", help="Do not descend into subfolders."
    )
    args = parser.parse_args()
    if not args.folder:
        sys.exit("Provide --folder <ID-or-URL> or set GDRIVE_FOLDER_ID.")
    sync(args.folder, recurse=not args.no_recurse)


if __name__ == "__main__":
    main()
