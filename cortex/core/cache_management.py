import json
import re
from datetime import datetime, timedelta, timezone
from typing import Any

from .database import get_redis_client

CACHE_TTL_SECONDS = 3600
CACHE_KEY_PATTERN = "rag_cache:*"
_DIGEST_PATTERN = re.compile(r"^[0-9a-f]{64}$")


def _decode_key(key: bytes | str) -> str:
    return key.decode("utf-8") if isinstance(key, bytes) else key


def _delete_keys(client: Any, keys: list[bytes | str]) -> int:
    deleted = 0
    for start in range(0, len(keys), 500):
        deleted += int(client.delete(*keys[start : start + 500]))
    return deleted


def clear_cache(pattern: str = CACHE_KEY_PATTERN) -> int:
    client = get_redis_client()
    return _delete_keys(client, list(client.scan_iter(pattern)))


def clear_knowledge_base_cache(slug: str) -> int:
    return clear_cache(f"rag_cache:{slug}:*")


def _validate_digest(digest: str) -> None:
    if not _DIGEST_PATTERN.fullmatch(digest):
        raise ValueError("Cache digest must be 64 lowercase hexadecimal characters.")


def delete_cache_entry(slug: str, digest: str) -> bool:
    _validate_digest(digest)
    return bool(get_redis_client().delete(f"rag_cache:{slug}:{digest}"))


def _parse_payload(value: bytes | str | None) -> tuple[str, dict[str, Any]]:
    if value is None:
        return "", {}
    payload_text = value.decode("utf-8") if isinstance(value, bytes) else value
    try:
        payload = json.loads(payload_text)
    except (TypeError, ValueError):
        payload = {}
    return payload_text, payload if isinstance(payload, dict) else {}


def _entry_from_values(
    key: str,
    value: bytes | str | None,
    ttl_seconds: int,
    size_bytes: int | None,
    now: datetime,
) -> dict[str, Any] | None:
    if ttl_seconds == -2 or value is None:
        return None
    payload_text, payload = _parse_payload(value)
    metadata = payload.get("_cache_meta") or {}
    query = metadata.get("query") or payload.get("audit", {}).get("retrieval_query")
    created_at = metadata.get("created_at")
    if not created_at and ttl_seconds >= 0:
        created_at = (
            now - timedelta(seconds=max(0, CACHE_TTL_SECONDS - ttl_seconds))
        ).isoformat()
    expires_at = (
        (now + timedelta(seconds=ttl_seconds)).isoformat()
        if ttl_seconds >= 0
        else None
    )
    parts = key.split(":", 2)
    slug = parts[1] if len(parts) == 3 else None
    digest = parts[-1]
    return {
        "digest": digest,
        "knowledge_base": slug,
        "query": str(query or "Query unavailable"),
        "created_at": created_at,
        "expires_at": expires_at,
        "ttl_seconds": ttl_seconds,
        "size_bytes": int(size_bytes or len(payload_text.encode("utf-8"))),
    }


def list_cache_entries(pattern: str = CACHE_KEY_PATTERN) -> list[dict[str, Any]]:
    client = get_redis_client()
    keys = list(client.scan_iter(pattern))
    if not keys:
        return []
    pipeline = client.pipeline(transaction=False)
    for key in keys:
        pipeline.get(key)
        pipeline.ttl(key)
        pipeline.memory_usage(key)
    values = pipeline.execute()
    now = datetime.now(timezone.utc)
    entries = []
    for index, raw_key in enumerate(keys):
        value, ttl_seconds, size_bytes = values[index * 3 : index * 3 + 3]
        entry = _entry_from_values(
            _decode_key(raw_key), value, int(ttl_seconds), size_bytes, now
        )
        if entry is not None:
            entries.append(entry)
    entries.sort(key=lambda item: (item["created_at"] or ""), reverse=True)
    return entries


def summarize_cache(entries: list[dict[str, Any]]) -> dict[str, Any]:
    ttl_values = [item["ttl_seconds"] for item in entries if item["ttl_seconds"] >= 0]
    return {
        "entry_count": len(entries),
        "size_bytes": sum(item["size_bytes"] for item in entries),
        "average_ttl_seconds": (
            round(sum(ttl_values) / len(ttl_values)) if ttl_values else None
        ),
        "expiring_soon_count": sum(0 <= ttl <= 300 for ttl in ttl_values),
    }


def get_global_cache_stats() -> dict[str, Any]:
    entries = list_cache_entries()
    knowledge_bases: dict[str, list[dict[str, Any]]] = {}
    for entry in entries:
        slug = entry["knowledge_base"] or "legacy"
        knowledge_bases.setdefault(slug, []).append(entry)
    return {
        **summarize_cache(entries),
        "knowledge_bases": {
            slug: summarize_cache(items)
            for slug, items in sorted(knowledge_bases.items())
        },
    }


def get_knowledge_base_cache(
    slug: str,
    offset: int,
    limit: int,
    query: str | None = None,
) -> dict[str, Any]:
    entries = list_cache_entries(f"rag_cache:{slug}:*")
    normalized_query = (query or "").strip().casefold()
    filtered_entries = (
        [item for item in entries if normalized_query in item["query"].casefold()]
        if normalized_query
        else entries
    )
    return {
        "knowledge_base": slug,
        "summary": summarize_cache(entries),
        "query": (query or "").strip(),
        "filtered_count": len(filtered_entries),
        "offset": offset,
        "limit": limit,
        "entries": filtered_entries[offset : offset + limit],
    }


def get_cache_entry_detail(slug: str, digest: str) -> dict[str, Any] | None:
    _validate_digest(digest)
    client = get_redis_client()
    key = f"rag_cache:{slug}:{digest}"
    pipeline = client.pipeline(transaction=False)
    pipeline.get(key)
    pipeline.ttl(key)
    pipeline.memory_usage(key)
    value, ttl_seconds, size_bytes = pipeline.execute()
    now = datetime.now(timezone.utc)
    entry = _entry_from_values(key, value, int(ttl_seconds), size_bytes, now)
    if entry is None:
        return None
    _, payload = _parse_payload(value)
    raw_audit = payload.get("audit")
    audit: dict[str, Any] = raw_audit if isinstance(raw_audit, dict) else {}
    contexts = payload.get("contexts") if isinstance(payload.get("contexts"), list) else []
    citations = payload.get("citations") if isinstance(payload.get("citations"), list) else []
    timings = audit.get("timings_ms") if isinstance(audit.get("timings_ms"), dict) else {}
    answer = payload.get("answer")
    return {
        **entry,
        "answer": str(answer) if answer is not None else None,
        "contexts": contexts,
        "citations": citations,
        "timings": timings,
    }
