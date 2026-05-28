import os
import re
import sys
import glob
import io
import httpx
import mysql.connector
from minio import Minio

# Environment variables loader (.env parser)
def load_env():
    env_path = os.path.join(os.path.dirname(__file__), ".env")
    if os.path.exists(env_path):
        with open(env_path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                parts = line.split("=", 1)
                if len(parts) == 2:
                    key = parts[0].strip()
                    val = parts[1].strip().strip('"').strip("'")
                    os.environ[key] = val

load_env()

# Configurations
INFINITY_HOST = os.getenv("INFINITY_HOST", "127.0.0.1")
INFINITY_HTTP_PORT = int(os.getenv("INFINITY_PORT", "23820"))
INFINITY_API_URL = f"http://{INFINITY_HOST}:{INFINITY_HTTP_PORT}"

MYSQL_HOST = os.getenv("MYSQL_HOST", "127.0.0.1")
MYSQL_PORT = int(os.getenv("MYSQL_PORT", "3306"))
MYSQL_USER = os.getenv("MYSQL_USER", "root")
MYSQL_PASS = os.getenv("MYSQL_PASS", "root")

MINIO_HOST = os.getenv("MINIO_HOST", "127.0.0.1:9000")
MINIO_USER = os.getenv("MINIO_USER", "minioadmin")
MINIO_PASS = os.getenv("MINIO_PASS", "minioadmin")

OLLAMA_HOST = os.getenv("OLLAMA_HOST", "127.0.0.1:11434")
OLLAMA_EMBED_URL = f"http://{OLLAMA_HOST}/api/embeddings"
OLLAMA_MODEL = "bge-m3:latest"


def parse_document_title(filename):
    base = os.path.basename(filename)
    # Strip numeric prefixes and lore suffixes common in Fate dataset, e.g. 123_Gawain_lore.md -> Gawain
    match = re.match(r"^\d+_(.*)_lore\.md$", base)
    if match:
        name_str = match.group(1)
        return name_str.replace("_", " ")
    # Otherwise general clean up
    return base.replace("_", " ").replace(".md", "")

def chunk_markdown(content, doc_title, max_chars=800):
    lines = content.split("\n")
    current_heading = "Overview"
    paragraphs = []
    current_para = []
    
    for line in lines:
        stripped = line.strip()
        if not stripped:
            if current_para:
                paragraphs.append((current_heading, "\n".join(current_para)))
                current_para = []
            continue
        
        heading_match = re.match(r"^(#+)\s+(.*)$", stripped)
        if heading_match:
            if current_para:
                paragraphs.append((current_heading, "\n".join(current_para)))
                current_para = []
            current_heading = heading_match.group(2).strip()
            continue
            
        current_para.append(line)
        
    if current_para:
        paragraphs.append((current_heading, "\n".join(current_para)))
        
    chunks = []
    current_chunk = []
    current_len = 0
    current_head = "Overview"
    
    for heading, text in paragraphs:
        prefix = f"[{doc_title} - {heading}] "
        full_text = prefix + text
        
        if current_len + len(full_text) > max_chars and current_chunk:
            chunks.append((current_head, "\n\n".join(current_chunk)))
            current_chunk = [full_text]
            current_len = len(full_text)
            current_head = heading
        else:
            current_chunk.append(full_text)
            current_len += len(full_text) + 2
            
    if current_chunk:
        chunks.append((current_head, "\n\n".join(current_chunk)))
        
    return chunks

def get_embedding(text):
    try:
        r = httpx.post(OLLAMA_EMBED_URL, json={"model": OLLAMA_MODEL, "prompt": text}, timeout=30.0)
        r.raise_for_status()
        return r.json()["embedding"]
    except Exception as e:
        print(f"Error calling Ollama embedding API: {e}")
        return None

def main():
    print("=== Starting Ingestion Pipeline (Direct HTTP) ===")
    
    # 1. Connect to MinIO
    print("Connecting to MinIO...")
    try:
        minio_client = Minio(
            MINIO_HOST,
            access_key=MINIO_USER,
            secret_key=MINIO_PASS,
            secure=False
        )
        bucket_name = "cortex-documents"
        if not minio_client.bucket_exists(bucket_name):
            minio_client.make_bucket(bucket_name)
            print(f"Created MinIO bucket '{bucket_name}'")
        else:
            print(f"MinIO bucket '{bucket_name}' already exists.")
    except Exception as e:
        print(f"FAILED to connect to MinIO: {e}")
        sys.exit(1)
        
    # 2. Connect to MySQL & Setup Relational DB
    print("Connecting to MySQL...")
    try:
        mysql_conn = mysql.connector.connect(
            host=MYSQL_HOST,
            port=MYSQL_PORT,
            user=MYSQL_USER,
            password=MYSQL_PASS
        )
        cursor = mysql_conn.cursor()
        cursor.execute("CREATE DATABASE IF NOT EXISTS cortex_rag")
        cursor.execute("USE cortex_rag")
        
        cursor.execute("""
        CREATE TABLE IF NOT EXISTS documents (
            id INT AUTO_INCREMENT PRIMARY KEY,
            filename VARCHAR(255) UNIQUE NOT NULL,
            title VARCHAR(255) NOT NULL,
            size_bytes INT NOT NULL,
            chunk_count INT NOT NULL
        )
        """)
        mysql_conn.commit()
        print("MySQL Database & Tables set up successfully.")
    except Exception as e:
        print(f"FAILED to connect/setup MySQL: {e}")
        sys.exit(1)
        
    # 3. Connect to Infinity & Setup Vector DB (HTTP REST API)
    print("Connecting to Infinity Vector DB (HTTP)...")
    try:
        headers = {"Content-Type": "application/json", "Accept": "application/json"}
        # Drop table if exists (using request with json={} body due to oatpp requirement)
        try:
            res_del = httpx.request(
                "DELETE",
                f"{INFINITY_API_URL}/databases/default_db/tables/chunks",
                json={},
                headers=headers,
                timeout=5.0
            )
            if res_del.status_code == 200:
                print("Dropped existing Infinity table 'chunks'.")
            else:
                print(f"Table drop info: {res_del.text}")
        except Exception as e:
            print(f"Ignore drop table warning: {e}")
            
        # Create Table chunks
        payload = {
            "fields": [
                {"name": "document_id", "type": "integer"},
                {"name": "chunk_index", "type": "integer"},
                {"name": "document_title", "type": "varchar"},
                {"name": "content", "type": "varchar"},
                {"name": "vec", "type": "vector, 1024, float"}
            ]
        }
        res = httpx.post(f"{INFINITY_API_URL}/databases/default_db/tables/chunks", json=payload, headers=headers, timeout=5.0)
        res.raise_for_status()
        if res.json().get("error_code", 0) != 0:
            raise Exception(res.json().get("error_msg", "Unknown error"))
            
        print("Infinity table 'chunks' created successfully.")
    except Exception as e:
        print(f"FAILED to connect/setup Infinity: {e}")
        sys.exit(1)
        
    # 4. Search and Process Markdown Files
    source_dir = "documents"
    if not os.path.exists(source_dir):
        source_dir = "servant_lore_md_v3"
        
    search_path = os.path.join(source_dir, "*.md")
    files = glob.glob(search_path)
    
    if not files:
        print(f"No markdown files found in '{source_dir}'. Exiting.")
        sys.exit(1)
        
    print(f"Found {len(files)} markdown files to process from '{source_dir}'.")
    
    doc_success_count = 0
    total_chunks_indexed = 0
    
    for file_idx, filepath in enumerate(files, 1):
        filename = os.path.basename(filepath)
        doc_title = parse_document_title(filename)
        
        print(f"[{file_idx}/{len(files)}] Processing '{filename}' as '{doc_title}'...")
        
        try:
            with open(filepath, "r", encoding="utf-8") as f:
                content = f.read()
                
            content_bytes = content.encode("utf-8")
            size_bytes = len(content_bytes)
            
            # A. Upload raw file to MinIO
            minio_client.put_object(
                bucket_name,
                filename,
                io.BytesIO(content_bytes),
                length=size_bytes,
                content_type="text/markdown"
            )
            
            # B. Chunk markdown
            chunks = chunk_markdown(content, doc_title)
            chunk_count = len(chunks)
            print(f"   -> Split into {chunk_count} semantic chunks.")
            
            # C. Register document in MySQL
            cursor.execute(
                "INSERT INTO documents (filename, title, size_bytes, chunk_count) VALUES (%s, %s, %s, %s) "
                "ON DUPLICATE KEY UPDATE title=%s, size_bytes=%s, chunk_count=%s",
                (filename, doc_title, size_bytes, chunk_count, doc_title, size_bytes, chunk_count)
            )
            mysql_conn.commit()
            
            cursor.execute("SELECT id FROM documents WHERE filename = %s", (filename,))
            doc_id = cursor.fetchone()[0]
            
            # D. Concurrently generate embeddings & insert into Infinity (HTTP)
            infinity_batch = []
            
            from concurrent.futures import ThreadPoolExecutor
            
            def embed_chunk(chunk_data):
                chunk_idx, heading, chunk_text = chunk_data
                emb = get_embedding(chunk_text)
                return chunk_idx, chunk_text, emb
 
            chunk_inputs = [(idx, head, text) for idx, (head, text) in enumerate(chunks)]
            
            # Use a pool of 16 threads for parallel Ollama embedding requests
            with ThreadPoolExecutor(max_workers=16) as executor:
                results = list(executor.map(embed_chunk, chunk_inputs))
                
            for chunk_idx, chunk_text, emb in results:
                if emb is None:
                    print(f"      [Warning] Failed to generate embedding for chunk {chunk_idx}. Skipping.")
                    continue
                
                infinity_batch.append({
                    "document_id": doc_id,
                    "chunk_index": chunk_idx,
                    "document_title": doc_title,
                    "content": chunk_text,
                    "vec": emb
                })
                
            if infinity_batch:
                # Direct HTTP POST to table/docs
                docs_res = httpx.post(
                    f"{INFINITY_API_URL}/databases/default_db/tables/chunks/docs",
                    json=infinity_batch,
                    headers=headers,
                    timeout=30.0
                )
                docs_res.raise_for_status()
                if docs_res.json().get("error_code", 0) != 0:
                    raise Exception(docs_res.json().get("error_msg"))
                total_chunks_indexed += len(infinity_batch)
                
            doc_success_count += 1
            
        except Exception as e:
            print(f"   [ERROR] Failed to ingest '{filename}': {e}")
            mysql_conn.rollback()
            
    cursor.close()
    mysql_conn.close()
    print(f"\n=== Ingestion Complete ===")
    print(f"Successfully processed {doc_success_count}/{len(files)} files.")
    print(f"Indexed a total of {total_chunks_indexed} chunks into Infinity Vector DB.")
    print("==========================")

if __name__ == "__main__":
    main()
