import re

import httpx

from .config import INFINITY_API_URL
from .database import (
    get_minio_client,
    get_mysql_connection,
    get_redis_client,
    list_knowledge_bases,
)


def _validate_vector_table(table_name: str):
    if not re.fullmatch(r"[a-z][a-z0-9_]{0,254}", table_name):
        raise ValueError(f"Invalid Infinity table name: {table_name}")


def ensure_vector_table(knowledge_base: dict, force_rebuild: bool = False):
    table_name = knowledge_base["vector_table"]
    _validate_vector_table(table_name)
    dimensions = int(
        knowledge_base["ingest_config"].get("embedding", {}).get("dimensions", 768)
    )
    headers = {"Content-Type": "application/json", "Accept": "application/json"}
    table_url = f"{INFINITY_API_URL}/databases/default_db/tables/{table_name}"

    if force_rebuild:
        response = httpx.request(
            "DELETE",
            table_url,
            json={},
            headers=headers,
            timeout=10.0,
        )
        if response.status_code >= 400 and response.status_code != 404:
            response.raise_for_status()

    response = httpx.get(table_url, headers=headers, timeout=5.0)
    table_exists = (
        response.status_code == 200
        and response.json().get("error_code", 0) == 0
    )
    table_created = not table_exists
    if table_created:
        response = httpx.post(
            table_url,
            json={
                "fields": [
                    {"name": "document_id", "type": "integer"},
                    {"name": "chunk_index", "type": "integer"},
                    {"name": "document_title", "type": "varchar"},
                    {"name": "content", "type": "varchar"},
                    {"name": "external_id", "type": "varchar", "default": ""},
                    {"name": "source_key", "type": "varchar", "default": ""},
                    {"name": "segment_ordinal", "type": "integer", "default": -1},
                    {"name": "segment_locator", "type": "varchar", "default": ""},
                    {"name": "vec", "type": f"vector, {dimensions}, float"},
                ]
            },
            headers=headers,
            timeout=10.0,
        )
        response.raise_for_status()
        body = response.json()
        if body.get("error_code", 0) != 0:
            raise RuntimeError(body.get("error_msg", "Infinity table creation failed"))

    index_response = httpx.post(
        f"{table_url}/indexes/content_fts",
        json={
            "create_option": "ignore_if_exists",
            "fields": ["content"],
            "index": {
                "type": "FULLTEXT",
                "params": {"analyzer": "standard"},
            },
        },
        headers=headers,
        timeout=10.0,
    )
    index_response.raise_for_status()
    if index_response.json().get("error_code", 0) != 0:
        raise RuntimeError(
            index_response.json().get("error_msg", "Infinity index creation failed")
        )
    return table_created


def ensure_external_vector_columns(knowledge_base: dict):
    """Add generic reader-document columns to a pre-existing Infinity table."""
    table_name = knowledge_base["vector_table"]
    _validate_vector_table(table_name)
    response = httpx.get(
        f"{INFINITY_API_URL}/databases/default_db/tables/{table_name}/columns",
        headers={"Accept": "application/json"},
        timeout=5.0,
    )
    response.raise_for_status()
    body = response.json()
    if body.get("error_code", 0) != 0:
        raise RuntimeError(body.get("error_msg", "Infinity column lookup failed"))
    existing = {
        name
        for column in body.get("columns", [])
        if (name := column.get("column_name") or column.get("name"))
    }
    definitions: dict[str, dict[str, str | int]] = {
        "external_id": {"type": "varchar", "default": ""},
        "source_key": {"type": "varchar", "default": ""},
        "segment_ordinal": {"type": "integer", "default": -1},
        "segment_locator": {"type": "varchar", "default": ""},
    }
    missing = {name: value for name, value in definitions.items() if name not in existing}
    if not missing:
        return

    response = httpx.post(
        f"{INFINITY_API_URL}/databases/default_db/tables/{table_name}/columns",
        json={
            "fields": [
                {"name": name, **definition}
                for name, definition in missing.items()
            ]
        },
        headers={"Content-Type": "application/json", "Accept": "application/json"},
        timeout=10.0,
    )
    response.raise_for_status()
    body = response.json()
    if body.get("error_code", 0) != 0:
        raise RuntimeError(body.get("error_msg", "Infinity column migration failed"))


def migrate_existing_vector_tables() -> int:
    """Bring vector tables created before reader scope support up to date."""
    knowledge_bases = list_knowledge_bases()
    for knowledge_base in knowledge_bases:
        ensure_external_vector_columns(knowledge_base)
    return len(knowledge_bases)


def ensure_minio_bucket(knowledge_base: dict):
    client = get_minio_client()
    bucket = knowledge_base["minio_bucket"]
    if not client.bucket_exists(bucket):
        client.make_bucket(bucket)


def clear_knowledge_base_cache(slug: str) -> int:
    client = get_redis_client()
    keys = list(client.scan_iter(f"rag_cache:{slug}:*"))
    if keys:
        client.delete(*keys)
    return len(keys)


def delete_knowledge_base_storage(knowledge_base: dict):
    table_name = knowledge_base["vector_table"]
    _validate_vector_table(table_name)
    response = httpx.request(
        "DELETE",
        f"{INFINITY_API_URL}/databases/default_db/tables/{table_name}",
        json={},
        headers={"Content-Type": "application/json", "Accept": "application/json"},
        timeout=10.0,
    )
    if response.status_code >= 400 and response.status_code != 404:
        response.raise_for_status()

    minio_client = get_minio_client()
    objects = minio_client.list_objects(
        knowledge_base["minio_bucket"],
        prefix=f"{knowledge_base['slug']}/",
        recursive=True,
    )
    for item in objects:
        minio_client.remove_object(knowledge_base["minio_bucket"], item.object_name)
    clear_knowledge_base_cache(knowledge_base["slug"])


def delete_source_storage(knowledge_base: dict, source_id: int):
    table_name = knowledge_base["vector_table"]
    _validate_vector_table(table_name)
    conn = get_mysql_connection()
    cursor = conn.cursor()
    try:
        cursor.execute(
            "SELECT id, minio_key FROM documents WHERE source_id = %s",
            (source_id,),
        )
        documents = cursor.fetchall()
    finally:
        cursor.close()
        conn.close()

    headers = {"Content-Type": "application/json", "Accept": "application/json"}
    minio_client = get_minio_client()
    for document_id, minio_key in documents:
        response = httpx.request(
            "DELETE",
            f"{INFINITY_API_URL}/databases/default_db/tables/{table_name}/docs",
            json={"filter": f"document_id = {int(document_id)}"},
            headers=headers,
            timeout=10.0,
        )
        response.raise_for_status()
        if minio_key:
            minio_client.remove_object(knowledge_base["minio_bucket"], minio_key)
    clear_knowledge_base_cache(knowledge_base["slug"])
