from pathlib import Path
from unittest.mock import MagicMock, patch

from cortex.core.kb_storage import (
    ensure_external_vector_columns,
    migrate_existing_vector_tables,
)


def test_migrate_existing_vector_tables_updates_every_knowledge_base():
    knowledge_bases = [
        {"slug": "alpha", "vector_table": "chunks_alpha"},
        {"slug": "beta", "vector_table": "chunks_beta"},
    ]
    with (
        patch("cortex.core.kb_storage.list_knowledge_bases", return_value=knowledge_bases),
        patch("cortex.core.kb_storage.ensure_external_vector_columns") as ensure_columns,
    ):
        migrated = migrate_existing_vector_tables()

    assert migrated == 2
    assert [call.args[0] for call in ensure_columns.call_args_list] == knowledge_bases


def test_vector_column_migration_uses_rest_api():
    columns_response = MagicMock()
    columns_response.json.return_value = {
        "error_code": 0,
        "columns": [{"name": "document_id"}, {"name": "content"}],
    }
    migration_response = MagicMock()
    migration_response.json.return_value = {"error_code": 0}

    with (
        patch("cortex.core.kb_storage.httpx.get", return_value=columns_response),
        patch("cortex.core.kb_storage.httpx.post", return_value=migration_response) as post,
    ):
        ensure_external_vector_columns({"vector_table": "chunks_alpha"})

    fields = post.call_args.kwargs["json"]["fields"]
    assert {field["name"] for field in fields} == {
        "external_id",
        "source_key",
        "segment_ordinal",
        "segment_locator",
    }


def test_management_ui_exposes_accessible_mobile_navigation():
    html = Path("static/index.html").read_text()

    assert 'id="tab-manage" aria-label="Manage"' in html
    assert 'class="send-btn" aria-label="Send Query"' in html
    assert 'id="manage-layout"' in html
