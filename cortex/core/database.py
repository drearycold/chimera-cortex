import mysql.connector
from minio import Minio
import redis
import httpx
import json
import os
from .config import (
    MYSQL_HOST, MYSQL_PORT, MYSQL_USER, MYSQL_PASS,
    MINIO_HOST, MINIO_USER, MINIO_PASS,
    REDIS_HOST, REDIS_PORT,
    INFINITY_API_URL, OLLAMA_HOST, RERANKER_HOST, RERANKER_PORT
)
from .kb_config import (
    DEFAULT_KB_SLUG,
    default_generation_config,
    default_ingest_config,
)


class KnowledgeBaseAlreadyExistsError(Exception):
    """Raised when a knowledge base slug is already registered."""


def _schema_object_exists(cursor, object_type: str, table: str, name: str) -> bool:
    query_by_type = {
        "column": (
            "SELECT 1 FROM INFORMATION_SCHEMA.COLUMNS "
            "WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = %s "
            "AND COLUMN_NAME = %s LIMIT 1"
        ),
        "index": (
            "SELECT 1 FROM INFORMATION_SCHEMA.STATISTICS "
            "WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = %s "
            "AND INDEX_NAME = %s LIMIT 1"
        ),
        "constraint": (
            "SELECT 1 FROM INFORMATION_SCHEMA.TABLE_CONSTRAINTS "
            "WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = %s "
            "AND CONSTRAINT_NAME = %s LIMIT 1"
        ),
    }
    cursor.execute(query_by_type[object_type], (table, name))
    return cursor.fetchone() is not None


def _add_column(cursor, table: str, name: str, definition: str):
    if not _schema_object_exists(cursor, "column", table, name):
        cursor.execute(f"ALTER TABLE {table} ADD COLUMN {name} {definition}")


def _add_document_column(cursor, name: str, definition: str):
    _add_column(cursor, "documents", name, definition)


def _bootstrap_default_knowledge_base(cursor) -> tuple[int, int]:
    ingest_config = json.dumps(default_ingest_config())
    generation_config = json.dumps(default_generation_config())
    cursor.execute("""
        INSERT IGNORE INTO knowledge_bases (
            slug, name, description, ingest_config, generation_config,
            vector_table, minio_bucket, enabled
        ) VALUES (%s, %s, %s, %s, %s, %s, %s, TRUE)
    """, (
        DEFAULT_KB_SLUG,
        "FGO Servant Lore",
        "Fate/Grand Order character lore and game mechanics",
        ingest_config,
        generation_config,
        "chunks_fgo_lore",
        "cortex-documents",
    ))
    cursor.execute("SELECT id FROM knowledge_bases WHERE slug = %s", (DEFAULT_KB_SLUG,))
    kb_id = cursor.fetchone()[0]

    cursor.execute("""
        SELECT id FROM sources
        WHERE kb_id = %s AND type = 'directory' AND name = 'Lore Files'
    """, (kb_id,))
    source = cursor.fetchone()
    if source:
        source_id = source[0]
    else:
        cursor.execute("""
            INSERT INTO sources (kb_id, type, name, config, sync_mode, enabled)
            VALUES (%s, 'directory', 'Lore Files', %s, 'manual', TRUE)
        """, (kb_id, json.dumps({"path": "documents", "glob_patterns": ["*.md"]})))
        source_id = cursor.lastrowid
    return kb_id, source_id


def get_mysql_connection(database="cortex_rag"):
    """Get a connection to the MySQL database."""
    return mysql.connector.connect(
        host=MYSQL_HOST,
        port=MYSQL_PORT,
        user=MYSQL_USER,
        password=MYSQL_PASS,
        database=database
    )

def get_minio_client():
    """Get a MinIO client instance."""
    return Minio(
        MINIO_HOST,
        access_key=MINIO_USER,
        secret_key=MINIO_PASS,
        secure=False
    )

def get_redis_client(socket_timeout=2):
    """Get a Redis client instance."""
    return redis.Redis(
        host=REDIS_HOST,
        port=REDIS_PORT,
        socket_timeout=socket_timeout
    )

def get_service_status():
    """Verify connectivity and health across all 6 core RAG services."""
    status = {
        "mysql": False,
        "minio": False,
        "redis": False,
        "infinity": False,
        "ollama": False,
        "reranker": False
    }
    
    # 1. Test MySQL
    try:
        conn = mysql.connector.connect(
            host=MYSQL_HOST,
            port=MYSQL_PORT,
            user=MYSQL_USER,
            password=MYSQL_PASS,
            database="cortex_rag",
            connection_timeout=2
        )
        if conn.is_connected():
            status["mysql"] = True
            conn.close()
    except Exception:
        pass

    # 2. Test MinIO
    try:
        minio_client = get_minio_client()
        minio_client.bucket_exists("cortex-documents")
        status["minio"] = True
    except Exception:
        pass

    # 3. Test Redis
    try:
        r = get_redis_client()
        if r.ping():
            status["redis"] = True
    except Exception:
        pass

    # 4. Test Infinity (HTTP REST API)
    try:
        r = httpx.get(f"{INFINITY_API_URL}/databases", timeout=2.0)
        if r.status_code == 200:
            status["infinity"] = True
    except Exception:
        pass

    # 5. Test Ollama
    try:
        r = httpx.get(f"http://{OLLAMA_HOST}/api/version", timeout=2.0)
        if r.status_code == 200:
            status["ollama"] = True
    except Exception:
        pass

    # 6. Test Reranker (llama-server)
    try:
        r = httpx.get(f"http://{RERANKER_HOST}:{RERANKER_PORT}/health", timeout=2.0)
        if r.status_code == 200 and r.json().get("status") == "ok":
            status["reranker"] = True
    except Exception:
        pass

    return status

def init_db():
    """Create required tables directly in cortex_rag."""
    try:
        # Connect directly with a short timeout to prevent locking startup
        conn = mysql.connector.connect(
            host=MYSQL_HOST,
            port=MYSQL_PORT,
            user=MYSQL_USER,
            password=MYSQL_PASS,
            database="cortex_rag",
            connection_timeout=3
        )
        cursor = conn.cursor()

        cursor.execute("""
        CREATE TABLE IF NOT EXISTS knowledge_bases (
            id INT AUTO_INCREMENT PRIMARY KEY,
            slug VARCHAR(64) UNIQUE NOT NULL,
            name VARCHAR(255) NOT NULL,
            description TEXT,
            ingest_config JSON NOT NULL,
            generation_config JSON NOT NULL,
            vector_table VARCHAR(255) UNIQUE NOT NULL,
            minio_bucket VARCHAR(255) NOT NULL,
            enabled BOOLEAN NOT NULL DEFAULT TRUE,
            created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
        )
        """)

        cursor.execute("""
        CREATE TABLE IF NOT EXISTS sources (
            id INT AUTO_INCREMENT PRIMARY KEY,
            kb_id INT NOT NULL,
            type VARCHAR(50) NOT NULL,
            name VARCHAR(255) NOT NULL,
            config JSON NOT NULL,
            sync_mode VARCHAR(50) NOT NULL DEFAULT 'manual',
            sync_cron VARCHAR(100),
            enabled BOOLEAN NOT NULL DEFAULT TRUE,
            last_synced_at TIMESTAMP NULL,
            created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (kb_id) REFERENCES knowledge_bases(id) ON DELETE CASCADE,
            INDEX idx_sources_kb_id (kb_id)
        )
        """)

        cursor.execute("""
        CREATE TABLE IF NOT EXISTS ingestion_log (
            id INT AUTO_INCREMENT PRIMARY KEY,
            kb_id INT NOT NULL,
            source_id INT,
            action VARCHAR(50) NOT NULL,
            docs_processed INT NOT NULL DEFAULT 0,
            docs_skipped INT NOT NULL DEFAULT 0,
            docs_failed INT NOT NULL DEFAULT 0,
            duration_seconds FLOAT NOT NULL DEFAULT 0.0,
            error_detail TEXT,
            created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (kb_id) REFERENCES knowledge_bases(id) ON DELETE CASCADE,
            FOREIGN KEY (source_id) REFERENCES sources(id) ON DELETE SET NULL,
            INDEX idx_ingestion_log_kb_id (kb_id),
            INDEX idx_ingestion_log_source_id (source_id)
        )
        """)
        
        # 1. documents table
        cursor.execute("""
        CREATE TABLE IF NOT EXISTS documents (
            id INT AUTO_INCREMENT PRIMARY KEY,
            source_id INT,
            filename VARCHAR(255) NOT NULL,
            title VARCHAR(255) NOT NULL,
            format VARCHAR(20) NOT NULL DEFAULT 'md',
            size_bytes INT NOT NULL,
            chunk_count INT NOT NULL,
            content_hash VARCHAR(64),
            status VARCHAR(20) NOT NULL DEFAULT 'pending',
            metadata JSON,
            external_id VARCHAR(512),
            source_key VARCHAR(512),
            origin_path VARCHAR(1024),
            minio_key VARCHAR(512),
            source_modified_at DATETIME,
            indexed_at DATETIME,
            created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
            UNIQUE KEY uq_documents_source_filename (source_id, filename),
            FOREIGN KEY (source_id) REFERENCES sources(id) ON DELETE CASCADE
        )
        """)

        _add_document_column(cursor, "content_hash", "VARCHAR(64)")
        _add_document_column(cursor, "source_id", "INT NULL AFTER id")
        _add_document_column(cursor, "format", "VARCHAR(20) NOT NULL DEFAULT 'md' AFTER title")
        _add_document_column(cursor, "status", "VARCHAR(20) NOT NULL DEFAULT 'pending'")
        _add_document_column(cursor, "metadata", "JSON NULL")
        _add_document_column(cursor, "external_id", "VARCHAR(512) NULL")
        _add_document_column(cursor, "source_key", "VARCHAR(512) NULL")
        _add_document_column(cursor, "origin_path", "VARCHAR(1024) NULL")
        _add_document_column(cursor, "minio_key", "VARCHAR(512) NULL")
        _add_document_column(cursor, "source_modified_at", "DATETIME NULL")
        _add_document_column(cursor, "indexed_at", "DATETIME NULL")
        _add_document_column(
            cursor,
            "created_at",
            "TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP",
        )
        _add_document_column(
            cursor,
            "updated_at",
            "TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP",
        )

        _, default_source_id = _bootstrap_default_knowledge_base(cursor)
        cursor.execute("""
            UPDATE documents
            SET source_id = %s,
                format = COALESCE(format, 'md'),
                status = CASE WHEN content_hash IS NULL THEN 'pending' ELSE 'indexed' END,
                minio_key = COALESCE(minio_key, filename),
                indexed_at = CASE
                    WHEN content_hash IS NOT NULL AND indexed_at IS NULL THEN CURRENT_TIMESTAMP
                    ELSE indexed_at
                END
            WHERE source_id IS NULL
        """, (default_source_id,))

        if _schema_object_exists(cursor, "index", "documents", "filename"):
            cursor.execute("ALTER TABLE documents DROP INDEX filename")
        if not _schema_object_exists(
            cursor,
            "index",
            "documents",
            "uq_documents_source_filename",
        ):
            cursor.execute(
                "ALTER TABLE documents ADD UNIQUE KEY "
                "uq_documents_source_filename (source_id, filename)"
            )
        if not _schema_object_exists(
            cursor,
            "index",
            "documents",
            "uq_documents_source_external_id",
        ):
            cursor.execute(
                "ALTER TABLE documents ADD UNIQUE KEY "
                "uq_documents_source_external_id (source_id, external_id)"
            )
        if not _schema_object_exists(
            cursor,
            "constraint",
            "documents",
            "fk_documents_source",
        ):
            cursor.execute("""
                ALTER TABLE documents
                ADD CONSTRAINT fk_documents_source
                FOREIGN KEY (source_id) REFERENCES sources(id) ON DELETE CASCADE
            """)


        # 2. benchmark_runs table
        cursor.execute("""
        CREATE TABLE IF NOT EXISTS benchmark_runs (
            id INT AUTO_INCREMENT PRIMARY KEY,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            dataset_name VARCHAR(255),
            kb_slug VARCHAR(64),
            judge_model VARCHAR(255),
            total_questions INT DEFAULT 0,
            duration_seconds FLOAT DEFAULT 0.0,
            avg_correctness FLOAT DEFAULT 0.0,
            avg_faithfulness FLOAT DEFAULT 0.0,
            avg_relevance FLOAT DEFAULT 0.0,
            pass_rate FLOAT DEFAULT 0.0,
            status VARCHAR(50) DEFAULT 'running',
            comment TEXT
        )
        """)

        # Ensure comment column exists for older database installations
        try:
            cursor.execute("ALTER TABLE benchmark_runs ADD COLUMN comment TEXT")
        except mysql.connector.Error as err:
            # 1060: Duplicate column name, meaning it already exists
            if err.errno != 1060:
                raise err
        _add_column(
            cursor,
            "benchmark_runs",
            "kb_slug",
            "VARCHAR(64) NULL AFTER dataset_name",
        )

        # 3. benchmark_results table
        cursor.execute("""
        CREATE TABLE IF NOT EXISTS benchmark_results (
            id INT AUTO_INCREMENT PRIMARY KEY,
            run_id INT NOT NULL,
            question_id VARCHAR(50),
            question TEXT,
            difficulty VARCHAR(50),
            reference_answer TEXT,
            rag_answer TEXT,
            cache_hit BOOLEAN,
            answer_correctness INT,
            faithfulness INT,
            retrieval_relevance INT,
            rationale TEXT,
            raw_judge_output TEXT,
            latency_embedding FLOAT,
            latency_retrieval FLOAT,
            latency_rerank FLOAT,
            latency_generation FLOAT,
            latency_total FLOAT,
            first_stage_candidates JSON,
            second_stage_candidates JSON,
            llm_prompt TEXT,
            FOREIGN KEY (run_id) REFERENCES benchmark_runs(id) ON DELETE CASCADE
        )
        """)
        conn.commit()
        cursor.close()
        conn.close()
        print("[DB] Tables verified/created successfully.")
    except Exception as e:
        print(f"[DB Warning] Database tables initialization bypassed or failed: {e}")


def _decode_knowledge_base(row: dict | None) -> dict | None:
    if row is None:
        return None

    decoded = dict(row)
    for field in ("ingest_config", "generation_config"):
        value = decoded.get(field)
        if isinstance(value, str):
            decoded[field] = json.loads(value)

    for field in ("created_at", "updated_at"):
        value = decoded.get(field)
        if value is not None and hasattr(value, "isoformat"):
            decoded[field] = value.isoformat()

    if "enabled" in decoded:
        decoded["enabled"] = bool(decoded["enabled"])

    source_count = int(decoded.pop("source_count", 0) or 0)
    document_count = int(decoded.pop("document_count", 0) or 0)
    chunk_count = int(decoded.pop("chunk_count", 0) or 0)
    last_indexed_at = decoded.pop("last_indexed_at", None)
    if last_indexed_at is not None and hasattr(last_indexed_at, "isoformat"):
        last_indexed_at = last_indexed_at.isoformat()
    decoded["stats"] = {
        "source_count": source_count,
        "document_count": document_count,
        "chunk_count": chunk_count,
        "last_indexed_at": last_indexed_at,
    }
    return decoded


def list_knowledge_bases() -> list[dict]:
    """List registered knowledge bases with currently available statistics."""
    conn = get_mysql_connection()
    cursor = conn.cursor(dictionary=True)
    try:
        cursor.execute("""
            SELECT kb.*,
                   (SELECT COUNT(*) FROM sources s WHERE s.kb_id = kb.id) AS source_count,
                   (SELECT COUNT(*) FROM documents d JOIN sources s ON d.source_id = s.id
                    WHERE s.kb_id = kb.id) AS document_count,
                   (SELECT COALESCE(SUM(d.chunk_count), 0) FROM documents d
                    JOIN sources s ON d.source_id = s.id WHERE s.kb_id = kb.id) AS chunk_count,
                   (SELECT MAX(d.indexed_at) FROM documents d JOIN sources s ON d.source_id = s.id
                    WHERE s.kb_id = kb.id) AS last_indexed_at
            FROM knowledge_bases kb
            ORDER BY kb.name ASC
        """)
        decoded_rows = []
        for row in cursor.fetchall():
            decoded = _decode_knowledge_base(row)
            if decoded is not None:
                decoded_rows.append(decoded)
        return decoded_rows
    finally:
        cursor.close()
        conn.close()


def get_knowledge_base(slug: str) -> dict | None:
    """Return one knowledge base by slug."""
    conn = get_mysql_connection()
    cursor = conn.cursor(dictionary=True)
    try:
        cursor.execute("""
            SELECT kb.*,
                   (SELECT COUNT(*) FROM sources s WHERE s.kb_id = kb.id) AS source_count,
                   (SELECT COUNT(*) FROM documents d JOIN sources s ON d.source_id = s.id
                    WHERE s.kb_id = kb.id) AS document_count,
                   (SELECT COALESCE(SUM(d.chunk_count), 0) FROM documents d
                    JOIN sources s ON d.source_id = s.id WHERE s.kb_id = kb.id) AS chunk_count,
                   (SELECT MAX(d.indexed_at) FROM documents d JOIN sources s ON d.source_id = s.id
                    WHERE s.kb_id = kb.id) AS last_indexed_at
            FROM knowledge_bases kb
            WHERE kb.slug = %s
        """, (slug,))
        return _decode_knowledge_base(cursor.fetchone())
    finally:
        cursor.close()
        conn.close()


def create_knowledge_base(data: dict) -> dict:
    """Register a knowledge base and derive its isolated storage names."""
    slug = data["slug"]
    vector_table = f"chunks_{slug.replace('-', '_')}"
    conn = get_mysql_connection()
    cursor = conn.cursor()
    try:
        cursor.execute("""
            INSERT INTO knowledge_bases (
                slug, name, description, ingest_config, generation_config,
                vector_table, minio_bucket, enabled
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
        """, (
            slug,
            data["name"],
            data.get("description"),
            json.dumps(data["ingest_config"]),
            json.dumps(data["generation_config"]),
            vector_table,
            "cortex-documents",
            data.get("enabled", True),
        ))
        conn.commit()
    except mysql.connector.IntegrityError as exc:
        conn.rollback()
        if exc.errno == 1062:
            raise KnowledgeBaseAlreadyExistsError(slug) from exc
        raise
    except Exception:
        conn.rollback()
        raise
    finally:
        cursor.close()
        conn.close()

    created = get_knowledge_base(slug)
    if created is None:
        raise RuntimeError(f"Knowledge base '{slug}' was not readable after creation.")
    return created


def update_knowledge_base(slug: str, changes: dict) -> dict | None:
    """Update mutable knowledge base metadata and configuration."""
    current = get_knowledge_base(slug)
    if current is None:
        return None
    name = changes.get("name") or current["name"]
    description = (
        changes["description"]
        if "description" in changes
        else current["description"]
    )
    ingest_config = changes.get("ingest_config") or current["ingest_config"]
    generation_config = (
        changes.get("generation_config") or current["generation_config"]
    )
    enabled = (
        changes["enabled"]
        if changes.get("enabled") is not None
        else current["enabled"]
    )
    conn = get_mysql_connection()
    cursor = conn.cursor()
    try:
        cursor.execute("""
            UPDATE knowledge_bases
            SET name = %s, description = %s, ingest_config = %s,
                generation_config = %s, enabled = %s
            WHERE slug = %s
        """, (
            name,
            description,
            json.dumps(ingest_config),
            json.dumps(generation_config),
            enabled,
            slug,
        ))
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        cursor.close()
        conn.close()

    return get_knowledge_base(slug)


def delete_knowledge_base(slug: str) -> bool:
    """Delete knowledge base metadata and its relational child records."""
    conn = get_mysql_connection()
    cursor = conn.cursor()
    try:
        cursor.execute("DELETE FROM knowledge_bases WHERE slug = %s", (slug,))
        deleted = cursor.rowcount > 0
        conn.commit()
        return deleted
    except Exception:
        conn.rollback()
        raise
    finally:
        cursor.close()
        conn.close()


def get_or_create_directory_source(kb_id: int, source_dir: str) -> dict:
    """Return a directory source for the KB, creating it when necessary."""
    normalized_path = os.path.normpath(source_dir)
    conn = get_mysql_connection()
    cursor = conn.cursor(dictionary=True)
    try:
        cursor.execute(
            "SELECT * FROM sources WHERE kb_id = %s AND type = 'directory' ORDER BY id",
            (kb_id,),
        )
        for source in cursor.fetchall():
            config = source["config"]
            if isinstance(config, str):
                config = json.loads(config)
            if config.get("path") == normalized_path:
                source["config"] = config
                return source

        config = {"path": normalized_path, "glob_patterns": ["*.md"]}
        cursor.execute("""
            INSERT INTO sources (kb_id, type, name, config, sync_mode, enabled)
            VALUES (%s, 'directory', %s, %s, 'manual', TRUE)
        """, (
            kb_id,
            f"Directory: {normalized_path}",
            json.dumps(config),
        ))
        source_id = cursor.lastrowid
        conn.commit()
        return {
            "id": source_id,
            "kb_id": kb_id,
            "type": "directory",
            "name": f"Directory: {normalized_path}",
            "config": config,
            "sync_mode": "manual",
            "enabled": True,
        }
    except Exception:
        conn.rollback()
        raise
    finally:
        cursor.close()
        conn.close()


def get_or_create_external_source(kb_id: int, source_key: str) -> dict:
    """Return the generic external source identified by an opaque source key."""
    conn = get_mysql_connection()
    cursor = conn.cursor(dictionary=True)
    try:
        cursor.execute(
            "SELECT * FROM sources WHERE kb_id = %s AND type = 'external' ORDER BY id",
            (kb_id,),
        )
        for source in cursor.fetchall():
            decoded = _decode_source(source)
            if decoded and decoded["config"].get("source_key") == source_key:
                return decoded

        config = {"source_key": source_key}
        cursor.execute(
            """
            INSERT INTO sources (kb_id, type, name, config, sync_mode, enabled)
            VALUES (%s, 'external', %s, %s, 'push', TRUE)
            """,
            (kb_id, source_key[:255], json.dumps(config)),
        )
        source_id = cursor.lastrowid
        conn.commit()
        return {
            "id": source_id,
            "kb_id": kb_id,
            "type": "external",
            "name": source_key[:255],
            "config": config,
            "sync_mode": "push",
            "enabled": True,
        }
    except Exception:
        conn.rollback()
        raise
    finally:
        cursor.close()
        conn.close()


def _decode_source(row: dict | None) -> dict | None:
    if row is None:
        return None
    source = dict(row)
    if isinstance(source.get("config"), str):
        source["config"] = json.loads(source["config"])
    source["enabled"] = bool(source.get("enabled"))
    for field in ("last_synced_at", "created_at"):
        value = source.get(field)
        if value is not None and hasattr(value, "isoformat"):
            source[field] = value.isoformat()
    return source


def list_sources(kb_id: int) -> list[dict]:
    conn = get_mysql_connection()
    cursor = conn.cursor(dictionary=True)
    try:
        cursor.execute(
            "SELECT * FROM sources WHERE kb_id = %s ORDER BY id",
            (kb_id,),
        )
        return [source for row in cursor.fetchall() if (source := _decode_source(row))]
    finally:
        cursor.close()
        conn.close()


def get_source(kb_id: int, source_id: int) -> dict | None:
    conn = get_mysql_connection()
    cursor = conn.cursor(dictionary=True)
    try:
        cursor.execute(
            "SELECT * FROM sources WHERE kb_id = %s AND id = %s",
            (kb_id, source_id),
        )
        return _decode_source(cursor.fetchone())
    finally:
        cursor.close()
        conn.close()


def create_source(kb_id: int, data: dict) -> dict:
    conn = get_mysql_connection()
    cursor = conn.cursor()
    try:
        cursor.execute(
            """
            INSERT INTO sources (
                kb_id, type, name, config, sync_mode, sync_cron, enabled
            ) VALUES (%s, %s, %s, %s, %s, %s, %s)
            """,
            (
                kb_id,
                data["type"],
                data["name"],
                json.dumps(data["config"]),
                data.get("sync_mode", "manual"),
                data.get("sync_cron"),
                data.get("enabled", True),
            ),
        )
        source_id = cursor.lastrowid
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        cursor.close()
        conn.close()
    created = get_source(kb_id, source_id)
    if created is None:
        raise RuntimeError(f"Source {source_id} was not readable after creation.")
    return created


def update_source(kb_id: int, source_id: int, changes: dict) -> dict | None:
    current = get_source(kb_id, source_id)
    if current is None:
        return None
    values = {
        "name": changes.get("name", current["name"]),
        "config": changes.get("config", current["config"]),
        "sync_mode": changes.get("sync_mode", current["sync_mode"]),
        "sync_cron": changes.get("sync_cron", current["sync_cron"]),
        "enabled": changes.get("enabled", current["enabled"]),
    }
    conn = get_mysql_connection()
    cursor = conn.cursor()
    try:
        cursor.execute(
            """
            UPDATE sources
            SET name = %s, config = %s, sync_mode = %s,
                sync_cron = %s, enabled = %s
            WHERE kb_id = %s AND id = %s
            """,
            (
                values["name"],
                json.dumps(values["config"]),
                values["sync_mode"],
                values["sync_cron"],
                values["enabled"],
                kb_id,
                source_id,
            ),
        )
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        cursor.close()
        conn.close()
    return get_source(kb_id, source_id)


def delete_source(kb_id: int, source_id: int) -> bool:
    conn = get_mysql_connection()
    cursor = conn.cursor()
    try:
        cursor.execute(
            "DELETE FROM sources WHERE kb_id = %s AND id = %s",
            (kb_id, source_id),
        )
        deleted = cursor.rowcount > 0
        conn.commit()
        return deleted
    except Exception:
        conn.rollback()
        raise
    finally:
        cursor.close()
        conn.close()


def list_scheduled_sources() -> list[dict]:
    conn = get_mysql_connection()
    cursor = conn.cursor(dictionary=True)
    try:
        cursor.execute(
            """
            SELECT s.*, kb.slug AS kb_slug
            FROM sources s
            JOIN knowledge_bases kb ON kb.id = s.kb_id
            WHERE s.enabled = TRUE AND kb.enabled = TRUE
              AND s.sync_mode = 'scheduled' AND s.sync_cron IS NOT NULL
            ORDER BY s.id
            """
        )
        sources = []
        for row in cursor.fetchall():
            kb_slug = row.pop("kb_slug")
            source = _decode_source(row)
            if source is not None:
                source["kb_slug"] = kb_slug
                sources.append(source)
        return sources
    finally:
        cursor.close()
        conn.close()


def record_ingestion_log(
    kb_slug: str,
    source_id: int | None,
    action: str,
    docs_processed: int,
    docs_skipped: int,
    docs_failed: int,
    duration_seconds: float,
    error_detail: str | None = None,
):
    conn = get_mysql_connection()
    cursor = conn.cursor()
    try:
        cursor.execute("SELECT id FROM knowledge_bases WHERE slug = %s", (kb_slug,))
        row = cursor.fetchone()
        if row is None:
            return
        cursor.execute(
            """
            INSERT INTO ingestion_log (
                kb_id, source_id, action, docs_processed, docs_skipped,
                docs_failed, duration_seconds, error_detail
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
            """,
            (
                row[0],
                source_id,
                action,
                docs_processed,
                docs_skipped,
                docs_failed,
                duration_seconds,
                error_detail,
            ),
        )
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        cursor.close()
        conn.close()


def list_ingestion_logs(kb_id: int, limit: int = 100) -> list[dict]:
    conn = get_mysql_connection()
    cursor = conn.cursor(dictionary=True)
    try:
        cursor.execute(
            """
            SELECT l.*, s.name AS source_name, s.type AS source_type
            FROM ingestion_log l
            LEFT JOIN sources s ON s.id = l.source_id
            WHERE l.kb_id = %s
            ORDER BY l.id DESC
            LIMIT %s
            """,
            (kb_id, min(500, max(1, limit))),
        )
        logs = cursor.fetchall()
        for log in logs:
            created_at = log.get("created_at")
            if created_at is not None and hasattr(created_at, "isoformat"):
                log["created_at"] = created_at.isoformat()
        return logs
    finally:
        cursor.close()
        conn.close()


def save_benchmark_run(
    dataset_name: str,
    judge_model: str,
    total_questions: int,
    comment: str | None = None,
    kb_slug: str | None = None,
) -> int:
    """Insert a new benchmark run and return its run_id."""
    conn = get_mysql_connection()
    cursor = conn.cursor()
    cursor.execute("""
        INSERT INTO benchmark_runs (
            dataset_name, kb_slug, judge_model, total_questions, status, comment
        ) VALUES (%s, %s, %s, %s, 'running', %s)
    """, (dataset_name, kb_slug, judge_model, total_questions, comment))
    conn.commit()
    run_id = cursor.lastrowid
    cursor.close()
    conn.close()
    return run_id

def update_benchmark_run_status(
    run_id: int, 
    status: str, 
    duration_seconds: float = 0.0, 
    avg_correctness: float = 0.0, 
    avg_faithfulness: float = 0.0, 
    avg_relevance: float = 0.0, 
    pass_rate: float = 0.0
):
    """Update status and aggregated scores of a benchmark run."""
    conn = get_mysql_connection()
    cursor = conn.cursor()
    cursor.execute("""
        UPDATE benchmark_runs
        SET status = %s, duration_seconds = %s, avg_correctness = %s, 
            avg_faithfulness = %s, avg_relevance = %s, pass_rate = %s
        WHERE id = %s
    """, (status, duration_seconds, avg_correctness, avg_faithfulness, avg_relevance, pass_rate, run_id))
    conn.commit()
    cursor.close()
    conn.close()

def save_benchmark_result(run_id: int, result: dict):
    """Insert a single question evaluation result."""
    conn = get_mysql_connection()
    cursor = conn.cursor()
    
    audit = result.get("audit") or {}
    timings = audit.get("timings_ms") or {}
    scores = result.get("scores") or {}
    
    first_stage = json.dumps(audit.get("first_stage_candidates") or [])
    second_stage = json.dumps(audit.get("second_stage_candidates") or [])
    
    cursor.execute("""
        INSERT INTO benchmark_results (
            run_id, question_id, question, difficulty, reference_answer, rag_answer, cache_hit,
            answer_correctness, faithfulness, retrieval_relevance, rationale, raw_judge_output,
            latency_embedding, latency_retrieval, latency_rerank, latency_generation, latency_total,
            first_stage_candidates, second_stage_candidates, llm_prompt
        ) VALUES (
            %s, %s, %s, %s, %s, %s, %s,
            %s, %s, %s, %s, %s,
            %s, %s, %s, %s, %s,
            %s, %s, %s
        )
    """, (
        run_id,
        result.get("id"),
        result.get("question"),
        result.get("difficulty"),
        result.get("reference_answer"),
        result.get("rag_answer"),
        result.get("cache_hit", False),
        scores.get("answer_correctness", 1),
        scores.get("faithfulness", 1),
        scores.get("retrieval_relevance", 1),
        scores.get("rationale", ""),
        scores.get("raw_judge_output", ""),
        timings.get("embedding", 0.0),
        timings.get("retrieval", 0.0),
        timings.get("rerank", 0.0),
        timings.get("generation", 0.0),
        timings.get("total", 0.0),
        first_stage,
        second_stage,
        audit.get("llm_prompt", "")
    ))
    conn.commit()
    cursor.close()
    conn.close()

def get_benchmark_runs() -> list:
    """Fetch all benchmark runs ordered by creation time descending."""
    conn = get_mysql_connection()
    cursor = conn.cursor(dictionary=True)
    cursor.execute("SELECT * FROM benchmark_runs ORDER BY created_at DESC")
    runs = cursor.fetchall()
    # Format created_at to string for JSON serialization compatibility
    for r in runs:
        if r.get("created_at"):
            r["created_at"] = r["created_at"].isoformat()
    cursor.close()
    conn.close()
    return runs

def get_benchmark_run(run_id: int) -> dict | None:
    """Fetch a benchmark run and all its associated question results."""
    conn = get_mysql_connection()
    cursor = conn.cursor(dictionary=True)
    
    cursor.execute("SELECT * FROM benchmark_runs WHERE id = %s", (run_id,))
    run = cursor.fetchone()
    if not run:
        cursor.close()
        conn.close()
        return None
        
    if run.get("created_at"):
        run["created_at"] = run["created_at"].isoformat()
        
    cursor.execute("SELECT * FROM benchmark_results WHERE run_id = %s ORDER BY id ASC", (run_id,))
    results = cursor.fetchall()
    
    # Parse candidate lists from JSON string
    for res in results:
        for json_col in ["first_stage_candidates", "second_stage_candidates"]:
            val = res.get(json_col)
            if isinstance(val, str):
                try:
                    res[json_col] = json.loads(val)
                except Exception:
                    res[json_col] = []
            elif val is None:
                res[json_col] = []
                
    run["results"] = results
    cursor.close()
    conn.close()
    return run

def delete_benchmark_run(run_id: int) -> bool:
    """Delete a benchmark run from database."""
    conn = get_mysql_connection()
    cursor = conn.cursor()
    cursor.execute("DELETE FROM benchmark_runs WHERE id = %s", (run_id,))
    conn.commit()
    affected = cursor.rowcount
    cursor.close()
    conn.close()
    return affected > 0
