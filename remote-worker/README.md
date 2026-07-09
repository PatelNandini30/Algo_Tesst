# LAN remote worker

Lets another in-house PC contribute its own CPU/RAM to backtest and optimize
jobs, in addition to the main box. No cloud, no auth — this is meant for a
trusted local network only. Do not port-forward these ports to the internet.

**The other PC does NOT need the project source code.** Everything (backend,
frontend, database) stays on the main box. This whole `remote-worker/` folder
— `docker-compose.yml`, `.env.example`, `algotest-worker-image.tar` (the
already-built backend image, a binary artifact, not source), and
`setup-windows.bat` — is everything that PC needs. Copy the folder over and
you're done; nothing else from the repository is required there.

## Windows quick start

1. Install Docker Desktop on the other PC (make sure it's running — whale
   icon in the system tray), and turn WSL2 integration on if prompted.
2. Copy this whole `remote-worker/` folder to that PC (USB, network share,
   whatever's easiest).
3. Double-click `setup-windows.bat`. First run creates `.env` and opens it in
   Notepad — set `NODE_IP` to that PC's own LAN IP (`ipconfig` → IPv4
   Address) and `NODE_CONCURRENCY` to how many cores to dedicate, save, close
   Notepad, then run `setup-windows.bat` again to actually load the image and
   start the worker.
4. Check `docker compose logs -f remote-worker` for `celery@... ready`, then
   look for it in the webapp's "Core:" dropdown.

The manual steps below are the same thing spelled out for Linux/Mac, or if
you'd rather run the commands yourself.

## What this does

Runs one Celery worker container on this PC that joins the main box's Redis
broker and Postgres over the LAN, on queues named after this PC's own IP
(`backtests@<this-pc-ip>`, `optimize@<this-pc-ip>`). It heartbeats its core
count and RAM into a shared registry (`services/node_registry.py`) every 15s
so the main box's webapp can show it in the navbar's "Core:" dropdown and
route jobs to it. Its own memory budget is sized from its own reported RAM —
it never competes with the main box's local memory budget.

## Manual setup (Linux/Mac, or if you'd rather not use the .bat)

### 1. On the main box (this PC) — the image is already exported here

`algotest-worker-image.tar` in this folder was built from
`algotest-backend-app:latest` via `docker save` and is already up to date as
of this writing. Re-run this after any backend code change, before copying
the folder again:
```bash
docker save algotest-backend-app:latest -o remote-worker/algotest-worker-image.tar
```
This box's LAN IP (already baked into `.env.example` as `MAIN_BOX_IP`):
```bash
hostname -I    # 192.168.4.34
```
Make sure the host firewall allows the LAN subnet to reach ports `5432`
(Postgres), `6379` (Redis), and `8000` (backend, for the tradesheet ZIP
fetch). Both Postgres and Redis already listen on `0.0.0.0` by default — no
`docker-compose.yml` change needed for LAN reachability, only firewall.

### 2. Get this whole folder onto the other PC

Copy the entire `remote-worker/` folder over (network share, USB, `scp` —
whatever's easiest on your LAN). It already contains everything needed.

### 3. On the other PC — load the image and configure

```bash
docker load -i algotest-worker-image.tar
cp .env.example .env
```
Edit `.env`:
- `MAIN_BOX_IP=192.168.4.34` (from step 1)
- `NODE_IP=<this PC's own LAN IP>` — find it with `hostname -I`
- `NODE_CONCURRENCY=2` (or however many cores to dedicate — this is what
  shows up in the webapp's "Core:" dropdown)
- Leave `POSTGRES_*` as-is unless the main box changed them from defaults.

### 4. Start it

```bash
docker compose up -d
```
(No `--build` — the image was already loaded in step 3.)

### 5. Verify

```bash
docker compose logs -f remote-worker      # look for "celery@... ready"
docker compose exec remote-worker celery -A worker.celery inspect ping
```
On the main box:
```bash
curl http://localhost:8000/api/system/nodes
```
should list this PC's IP within ~15 seconds, and it'll appear in the
webapp's "Core:" dropdown when opened from any browser on the LAN.

## Updating after a code change

Whenever the main box's backend image is rebuilt (new feature/fix), redo
steps 1–4 on each remote PC: `docker save` the new image, transfer the tar,
`docker load` it (same tag, overwrites), then `docker compose up -d` again
to recreate the container on the new image.

## Notes

- If this PC's browser opens the webapp, its own node is marked "this PC" in
  the dropdown (matched by request IP) — but the job still runs wherever the
  dropdown selection points, not necessarily on the browsing PC itself.
- Stopping this container (`docker compose down`) simply lets its heartbeat
  expire (~45s) — it disappears from the dropdown automatically, no manual
  cleanup needed. Any job it was running gets reclaimed via the memory-gate's
  TTL (~40 min) if killed mid-job — same no-OOM safety net as the main box.
- To dedicate more/fewer cores later, edit `NODE_CONCURRENCY`/`NODE_CPU_LIMIT`
  in `.env` and `docker compose up -d` again.
