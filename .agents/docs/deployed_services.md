# Deployed Infrastructure Summary

This document serves as a persistent record of the services and AI models deployed across your network hosts.

---

## 1. Services on 192.168.11.35 (Podman)

These services are deployed via `podman-compose` under `/mnt/pool/app/chimera_os/`. Due to the absence of the `ip_tables` module on the Gentoo host kernel, all services have been configured with **Host Networking** (`network_mode: host`) to bypass standard port-forwarding limitations.

### Summary of Services

| Service | Directory Path | Image | Ports | Default Credentials | Status |
| :--- | :--- | :--- | :--- | :--- | :--- |
| **Redis** | `/mnt/pool/app/chimera_os/redis` | `docker.io/library/redis:alpine` | `6379` | None | Running |
| **MinIO** | `/mnt/pool/app/chimera_os/minio` | `docker.io/minio/minio:latest` | `9000` (API)<br>`9001` (Console) | `minioadmin` / `minioadmin` | Running |
| **MySQL** | `/mnt/pool/app/chimera_os/mysql` | `docker.io/library/mysql:8.0` | `3306` | `root` / `root` | Running |
| **Infinity** | `/mnt/pool/app/chimera_os/infinity` | `docker.io/infiniflow/infinity:v0.7.0` | `23820` (HTTP)<br>`23817` (Thrift)<br>`5432` (PSQL) | None | Running |

---

## 2. LLM Infrastructure on 192.168.11.40 (Ollama)

Ollama is running as a `systemd` service and has been configured to be accessible across the local network.

### Summary of Ollama Config

| Host | Port | Configuration File | Active Proxies | Listening Interfaces | Status |
| :--- | :--- | :--- | :--- | :--- | :--- |
| `192.168.11.40` | `11434` | `/etc/ollama.conf` | `http://peter-proxy.lan:8118` | `*` (All interfaces via `0.0.0.0`) | Active |

### Active Models on 192.168.11.40

| Model Name | Tag / Version | Size | Purpose | Status |
| :--- | :--- | :--- | :--- | :--- |
| **`Sakura-Galtransl-7B-v3.7`** | `latest` | `6.3 GB` | Translation | Running |
| **`qwen2.5:3b`** | `latest` | `1.9 GB` | Light-weight General Text Generation | Running |
| **`bge-m3`** | `latest` | `1.2 GB` | Text Embeddings & Retrieval | Running |

---

## Detailed Service Configurations

### 1. Redis (`192.168.11.35`)
- **Path**: `/mnt/pool/app/chimera_os/redis`
- **Volume Mount**: `./data` -> `/data`
- **Compose Configuration**:
```yaml
version: "3.8"
services:
  redis:
    image: docker.io/library/redis:alpine
    container_name: redis
    restart: unless-stopped
    network_mode: host
    volumes:
      - ./data:/data
```

### 2. MinIO (`192.168.11.35`)
- **Path**: `/mnt/pool/app/chimera_os/minio`
- **Volume Mount**: `./data` -> `/data`
- **Console Address**: Configured to run on port `:9001`
- **Compose Configuration**:
```yaml
version: "3.8"
services:
  minio:
    image: docker.io/minio/minio:latest
    container_name: minio
    restart: unless-stopped
    network_mode: host
    environment:
      MINIO_ROOT_USER: minioadmin
      MINIO_ROOT_PASSWORD: minioadmin
    volumes:
      - ./data:/data
    command: server /data --console-address ":9001"
```

### 3. MySQL (`192.168.11.35`)
- **Path**: `/mnt/pool/app/chimera_os/mysql`
- **Volume Mount**: `./data` -> `/var/lib/mysql`
- **Compose Configuration**:
```yaml
version: "3.8"
services:
  mysql:
    image: docker.io/library/mysql:8.0
    container_name: mysql
    restart: unless-stopped
    network_mode: host
    environment:
      MYSQL_ROOT_PASSWORD: root
    volumes:
      - ./data:/var/lib/mysql
```

### 4. Infinity Vector DB (`192.168.11.35`)
- **Path**: `/mnt/pool/app/chimera_os/infinity`
- **Volume Mount**: `./data` -> `/var/infinity`
- **Compose Configuration**:
```yaml
version: "3.8"
services:
  infinity:
    image: docker.io/infiniflow/infinity:v0.7.0
    container_name: infinity
    restart: unless-stopped
    network_mode: host
    volumes:
      - ./data:/var/infinity
```

### 5. Ollama Configuration (`192.168.11.40`)
- **Systemd configuration file**: `/etc/ollama.conf`
- **Key Settings**:
```ini
OLLAMA_HOST="0.0.0.0:11434"
HTTP_PROXY="http://peter-proxy.lan:8118"
HTTPS_PROXY="http://peter-proxy.lan:8118"
```

---

> [!WARNING]
> Remember to change the default credentials for **MinIO** and **MySQL** on `192.168.11.35` for production security.
