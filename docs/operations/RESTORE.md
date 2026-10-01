# Restore Guide — New PC Setup

## Prerequisites
- Docker + Docker Compose installed
- At least 100 GB free disk space
- Backup folder mounted / accessible

---

## Step 1 — Copy backup files to new PC

Copy the entire dated backup folder (e.g. `2026-05-28/`) from the NAS to a local temp folder, e.g. `/tmp/algo_backup/`.

---

## Step 2 — Extract source code

```bash
sudo mkdir -p /home/user/Algo_Test_Software
sudo tar xzf /tmp/algo_backup/source_code.tar.gz -C /home/user/
sudo chown -R $USER:$USER /home/user/Algo_Test_Software
```

---

## Step 3 — Extract cleaned_csvs and other data

```bash
cd /home/user/Algo_Test_Software
tar xzf /tmp/algo_backup/cleaned_csvs.tar.gz
tar xzf /tmp/algo_backup/other_data.tar.gz
```

---

## Step 4 — Restore PostgreSQL database

```bash
# Start only postgres first
cd /home/user/Algo_Test_Software
docker compose up -d postgres

# Wait ~20 seconds for postgres to become healthy
sleep 20

# Restore the SQL dump (this may take 10-30 min depending on data size)
gunzip -c /tmp/algo_backup/pgdump.sql.gz \
  | docker compose exec -T postgres psql -U algotest algotest

echo "Database restored!"
```

---

## Step 5 — Restore algo_cache volume (optional but saves warm-up time)

```bash
docker volume create algo_test_software_algo_cache

docker run --rm \
  -v algo_test_software_algo_cache:/data \
  -v /tmp/algo_backup:/backup \
  alpine tar xzf /backup/algo_cache.tar.gz -C /data
```

---

## Step 6 — Start everything

```bash
cd /home/user/Algo_Test_Software
docker compose up -d --build
```

This builds the backend/worker images (10-15 min first time) and starts all containers.

After it's up, open: http://localhost:3000

---

## Step 7 — Verify

```bash
# Check all containers are healthy
docker compose ps

# Check DB has data
docker compose exec postgres psql -U algotest -c "SELECT COUNT(*) FROM option_data;"
```

---

## What each backup file contains

| File | Contents |
|------|----------|
| `pgdump.sql.gz` | Full PostgreSQL database (all option data, users, schema) |
| `source_code.tar.gz` | All code — backend, frontend, configs, docker-compose.yml |
| `cleaned_csvs.tar.gz` | Raw CSV source files (14 GB original) |
| `other_data.tar.gz` | expiryData, strikeData, Filter, reports, sample data |
| `algo_cache.tar.gz` | Arrow/feather cache (speeds up first backtest) |
