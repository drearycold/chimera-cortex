#!/usr/bin/env python3
import argparse

from cortex.core.database import get_knowledge_base, get_minio_client, get_mysql_connection
from cortex.core.kb_config import DEFAULT_KB_SLUG
from cortex.core.kb_storage import clear_knowledge_base_cache, ensure_vector_table


def clean_index(kb_slug: str):
    knowledge_base = get_knowledge_base(kb_slug)
    if knowledge_base is None:
        raise ValueError(f"Knowledge base '{kb_slug}' does not exist.")

    print(f"=== Cleaning Chimera Cortex Index: {kb_slug} ===")
    conn = get_mysql_connection()
    cursor = conn.cursor()
    try:
        cursor.execute("""
            DELETE d FROM documents d
            JOIN sources s ON d.source_id = s.id
            WHERE s.kb_id = %s
        """, (knowledge_base["id"],))
        deleted_documents = cursor.rowcount
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        cursor.close()
        conn.close()
    print(f"[SUCCESS] Deleted {deleted_documents} MySQL document records.")

    minio_client = get_minio_client()
    objects = list(
        minio_client.list_objects(
            knowledge_base["minio_bucket"],
            prefix=f"{kb_slug}/",
            recursive=True,
        )
    )
    for item in objects:
        minio_client.remove_object(knowledge_base["minio_bucket"], item.object_name)
    print(f"[SUCCESS] Deleted {len(objects)} namespaced MinIO objects.")

    ensure_vector_table(knowledge_base, force_rebuild=True)
    print(f"[SUCCESS] Recreated Infinity table '{knowledge_base['vector_table']}'.")
    cleared = clear_knowledge_base_cache(kb_slug)
    print(f"[SUCCESS] Cleared {cleared} Redis cache entries.")


def main():
    parser = argparse.ArgumentParser(description="Clear one knowledge base index.")
    parser.add_argument(
        "--kb",
        default=DEFAULT_KB_SLUG,
        help=f"Knowledge base slug to clear (default: {DEFAULT_KB_SLUG})",
    )
    args = parser.parse_args()
    clean_index(args.kb)


if __name__ == "__main__":
    main()
