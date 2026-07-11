import unittest
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import Mock

import httpx
from fastapi import HTTPException

from cortex.api.sources import _validate_source_config
from cortex.core.connectors import (
    DropboxConnector,
    GoogleDriveConnector,
    OneDriveConnector,
)
from cortex.core.connectors.cloud_common import stable_cloud_filename
from cortex.core.ingest import document_identity


class PhaseFourCloudDriveTests(unittest.TestCase):
    def test_cloud_filename_is_stable_across_provider_rename(self):
        before = stable_cloud_filename("provider-file-1", "Old Name.md")
        after = stable_cloud_filename("provider-file-1", "Renamed Note.md")

        self.assertEqual(before, after)
        self.assertTrue(before.endswith(".md"))

    def test_cloud_ingestion_matches_existing_document_by_provider_identity(self):
        raw_document = SimpleNamespace(
            source_type="cloud_drive",
            origin_path="google_drive:file-1",
            filename="cloud-file.md",
        )

        self.assertEqual(
            ("origin_path", "google_drive:file-1"),
            document_identity(raw_document),
        )

    def test_google_drive_full_scan_exports_and_records_cursor(self):
        files_api = Mock()
        files_api.list.return_value.execute.return_value = {
            "files": [
                {
                    "id": "g-1",
                    "name": "Reader Notes",
                    "mimeType": "application/vnd.google-apps.document",
                    "modifiedTime": "2026-07-11T00:00:00Z",
                    "parents": ["folder"],
                }
            ]
        }
        files_api.export.return_value.execute.return_value = b"Grounded cloud note."
        changes_api = Mock()
        changes_api.getStartPageToken.return_value.execute.return_value = {
            "startPageToken": "google-cursor-1"
        }
        service = Mock()
        service.files.return_value = files_api
        service.changes.return_value = changes_api
        connector = GoogleDriveConnector(
            1,
            2,
            {
                "folder_id": "folder",
                "token_env": "GOOGLE_TOKEN",
                "recursive": True,
            },
            service=service,
        )

        documents = connector.scan()

        self.assertTrue(connector.is_full_snapshot)
        self.assertEqual("google-cursor-1", connector.next_cursor)
        self.assertEqual(1, len(documents))
        self.assertEqual("google_drive:g-1", documents[0].origin_path)
        self.assertIn("Grounded cloud note", documents[0].content_markdown)
        files_api.export.assert_called_once_with(fileId="g-1", mimeType="text/plain")

    def test_google_drive_incremental_removed_change_uses_opaque_origin(self):
        changes_api = Mock()
        changes_api.list.return_value.execute.return_value = {
            "changes": [{"removed": True, "fileId": "gone-file"}],
            "newStartPageToken": "google-cursor-2",
        }
        service = Mock()
        service.changes.return_value = changes_api
        connector = GoogleDriveConnector(
            1,
            2,
            {
                "folder_id": "folder",
                "folder_ids": ["folder"],
                "token_env": "GOOGLE_TOKEN",
                "cursor": "google-cursor-1",
            },
            service=service,
        )

        documents = connector.scan()

        self.assertFalse(connector.is_full_snapshot)
        self.assertEqual([], documents)
        self.assertEqual(["google_drive:gone-file"], connector.deleted_origin_paths)
        self.assertEqual("google-cursor-2", connector.next_cursor)

    def test_google_drive_incremental_move_out_of_scope_is_deleted(self):
        changes_api = Mock()
        changes_api.list.return_value.execute.return_value = {
            "changes": [
                {
                    "fileId": "moved-file",
                    "file": {
                        "id": "moved-file",
                        "name": "Moved.md",
                        "mimeType": "text/markdown",
                        "parents": ["somewhere-else"],
                    },
                }
            ],
            "newStartPageToken": "google-cursor-2",
        }
        service = Mock()
        service.changes.return_value = changes_api
        connector = GoogleDriveConnector(
            1,
            2,
            {
                "folder_id": "folder",
                "folder_ids": ["folder"],
                "token_env": "GOOGLE_TOKEN",
                "cursor": "google-cursor-1",
            },
            service=service,
        )

        documents = connector.scan()

        self.assertEqual([], documents)
        self.assertEqual(
            ["google_drive:moved-file"],
            connector.deleted_origin_paths,
        )

    def test_google_drive_download_failure_fails_sync_for_cursor_retry(self):
        files_api = Mock()
        files_api.list.return_value.execute.return_value = {
            "files": [
                {
                    "id": "g-broken",
                    "name": "Broken.md",
                    "mimeType": "text/markdown",
                    "modifiedTime": "2026-07-11T00:00:00Z",
                    "parents": ["folder"],
                }
            ]
        }
        files_api.get_media.return_value.execute.side_effect = RuntimeError(
            "download failed"
        )
        changes_api = Mock()
        changes_api.getStartPageToken.return_value.execute.return_value = {
            "startPageToken": "google-cursor-1"
        }
        service = Mock()
        service.files.return_value = files_api
        service.changes.return_value = changes_api
        connector = GoogleDriveConnector(
            1,
            2,
            {"folder_id": "folder", "token_env": "GOOGLE_TOKEN"},
            service=service,
        )

        with self.assertRaisesRegex(RuntimeError, "Broken.md"):
            connector.scan()

    def test_onedrive_delta_downloads_file_and_records_delta_link(self):
        def handler(request: httpx.Request):
            if request.url.path.endswith("/delta"):
                return httpx.Response(
                    200,
                    json={
                        "value": [
                            {
                                "id": "o-1",
                                "name": "Architecture.md",
                                "lastModifiedDateTime": "2026-07-11T00:00:00Z",
                                "file": {"mimeType": "text/markdown"},
                                "@microsoft.graph.downloadUrl": "https://download.test/o-1",
                            }
                        ],
                        "@odata.deltaLink": "https://graph.microsoft.com/v1.0/delta-token",
                    },
                    request=request,
                )
            if request.url.host == "download.test":
                return httpx.Response(200, content=b"# Cloud Architecture\n\nDelta sync.", request=request)
            return httpx.Response(404, request=request)

        client = httpx.Client(
            base_url="https://graph.microsoft.com/v1.0",
            transport=httpx.MockTransport(handler),
        )
        connector = OneDriveConnector(
            1,
            2,
            {"drive_id": "drive", "folder_id": "folder", "token_env": "MS_TOKEN"},
            client=client,
        )

        documents = connector.scan()

        self.assertEqual(1, len(documents))
        self.assertEqual("onedrive:o-1", documents[0].origin_path)
        self.assertEqual(
            "https://graph.microsoft.com/v1.0/delta-token",
            connector.next_cursor,
        )

    def test_dropbox_cursor_downloads_and_reports_deleted_paths(self):
        FileMetadata = type("FileMetadata", (), {})
        DeletedMetadata = type("DeletedMetadata", (), {})
        file_entry = FileMetadata()
        file_entry.name = "Notes.txt"
        file_entry.path_lower = "/kb/notes.txt"
        file_entry.path_display = "/KB/Notes.txt"
        file_entry.rev = "rev-1"
        file_entry.server_modified = datetime(2026, 7, 11, tzinfo=timezone.utc)
        deleted_entry = DeletedMetadata()
        deleted_entry.path_lower = "/kb/old.txt"
        result = SimpleNamespace(
            entries=[file_entry, deleted_entry],
            cursor="dropbox-cursor-2",
            has_more=False,
        )
        client = Mock()
        client.files_list_folder_continue.return_value = result
        client.files_download.return_value = (Mock(), SimpleNamespace(content=b"Dropbox note."))
        connector = DropboxConnector(
            1,
            2,
            {"path": "/kb", "token_env": "DROPBOX_TOKEN", "cursor": "dropbox-cursor-1"},
            client=client,
        )

        documents = connector.scan()

        self.assertEqual(1, len(documents))
        self.assertEqual("dropbox:/kb/notes.txt", documents[0].origin_path)
        self.assertEqual(["dropbox:/kb/old.txt"], connector.deleted_origin_paths)
        self.assertEqual("dropbox-cursor-2", connector.next_cursor)

    def test_cloud_source_validation_requires_env_backed_credentials(self):
        with self.assertRaises(HTTPException):
            _validate_source_config(
                "cloud_drive",
                {
                    "provider": "google_drive",
                    "folder_id": "folder",
                    "access_token": "secret",
                },
                "manual",
                None,
            )
        with self.assertRaises(HTTPException):
            _validate_source_config(
                "cloud_drive",
                {"provider": "onedrive", "drive_id": "drive"},
                "manual",
                None,
            )
        _validate_source_config(
            "cloud_drive",
            {
                "provider": "dropbox",
                "path": "/kb",
                "token_env": "DROPBOX_TOKEN",
            },
            "scheduled",
            "0 */6 * * *",
        )


if __name__ == "__main__":
    unittest.main()
