# Isolation Contract

## Live resources that are forbidden for writes

| Resource | Live value | Shadow value |
|---|---|---|
| Frontend port | 3000 | 3200 |
| API port | 8000 | 8200 |
| PostgreSQL port/database | 5432 / algotest | 55432 / algotest_rust_shadow |
| Redis port/database | 6379 / 0 | 6380 / 0 (separate instance) |
| Compose project | default/current | algotest-rust-shadow |
| Container prefix | algotest- | algotest-rust-shadow- |
| Network | algotest-network | algotest-rust-shadow-network |
| Artifacts | /data/cache/optim_* | /rust-shadow-data/artifacts |
| Writable cache | /data/cache | /rust-shadow-data/cache |

Market-data snapshots may be copied from production only after they are closed
and checksummed. A live market cache may only be mounted read-only. A parity run
must record the exact snapshot manifest used by both expected and actual output.

## Live-file baseline (captured before implementation)

The following SHA-256 values were captured before creating this directory:

```text
61864cf1c230f4a98cdb3eda8cae3424167403cca9e7fc0a64474284a92e0d39  docker-compose.yml
20220acdfe0c7fc4ae7a80080aed22027b0a40e698e8c4f5fbc63185d781f2bb  start.sh
37517d7ec9b68a0e334b62d3ae6cdc5ba88d5adf9b9a71224182c98b560c7a4f  backend/worker/tasks.py
cd88c1baa0a602b30a65328f58944b090524dd4bd23de6e7a5e1658016ee6df0  backend/services/optimizer/runner.py
5ec05ce59240d57787020ef97d569919b77336ec5caa1bf6903039fbed47573b  backend/native/src/optimizer.rs
```

These values are audit evidence, not a demand to revert pre-existing user work.
Only this implementation's changes are prohibited from altering those files.

## Runtime rules

1. Never invoke the production `start.sh`, Compose project, or Celery workers.
2. Never connect a shadow worker to the production Redis broker.
3. Never write into a production PostgreSQL schema.
4. Never build in `backend/native/target`; this workspace owns its own `target`.
5. Never report a partial combination set as success.
6. Prefer a dedicated LAN parity node. The production 16 GB desktop is not a
   safe place for full-speed shadow workloads while live work is continuous.

