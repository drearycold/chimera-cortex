import os

# Environment variables loader (.env parser)
def load_env():
    env_path = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), ".env")
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
                    os.environ.setdefault(key, val)

load_env()

# Configurations
INFINITY_HOST = os.getenv("INFINITY_HOST", "127.0.0.1")
INFINITY_PORT = int(os.getenv("INFINITY_PORT", "23820"))
INFINITY_API_URL = f"http://{INFINITY_HOST}:{INFINITY_PORT}"
DEFAULT_API_URL = "http://127.0.0.1:8000"

MYSQL_HOST = os.getenv("MYSQL_HOST", "127.0.0.1")
MYSQL_PORT = int(os.getenv("MYSQL_PORT", "3306"))
MYSQL_USER = os.getenv("MYSQL_USER", "root")
MYSQL_PASS = os.getenv("MYSQL_PASS", "root")

MINIO_HOST = os.getenv("MINIO_HOST", "127.0.0.1:9000")
MINIO_USER = os.getenv("MINIO_USER", "minioadmin")
MINIO_PASS = os.getenv("MINIO_PASS", "minioadmin")

REDIS_HOST = os.getenv("REDIS_HOST", "127.0.0.1")
REDIS_PORT = int(os.getenv("REDIS_PORT", "6379"))

OLLAMA_HOST = os.getenv("OLLAMA_HOST", "127.0.0.1:11434")
OLLAMA_EMBED_URL = f"http://{OLLAMA_HOST}/api/embeddings"
OLLAMA_GENERATE_URL = f"http://{OLLAMA_HOST}/api/generate"
OLLAMA_EMBED_MODEL = "bge-m3:latest"
OLLAMA_GEN_MODEL = "qwen2.5:3b"
JUDGE_OLLAMA_HOST = os.getenv("JUDGE_OLLAMA_HOST", "192.168.11.60:11434")
DEFAULT_OLLAMA_HOST = JUDGE_OLLAMA_HOST
DEFAULT_JUDGE_MODEL = "qwen3.5:9b"

# Reranker configurations (llama-server in rerank mode)
RERANKER_HOST = os.getenv("RERANKER_HOST", "127.0.0.1")
RERANKER_PORT = int(os.getenv("RERANKER_PORT", "8082"))
RERANKER_URL = f"http://{RERANKER_HOST}:{RERANKER_PORT}/v1/rerank"
RERANKER_HEALTH_URL = f"http://{RERANKER_HOST}:{RERANKER_PORT}/health"

# Benchmark specific directories
RESULTS_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), "benchmark_results")
DEFAULT_DATASET = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), "benchmark_dataset.json")
