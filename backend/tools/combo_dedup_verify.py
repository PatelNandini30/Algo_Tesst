"""Prove the dedup fingerprint against a COMPLETED job's real results.

Groups every stored result row by effective_fingerprint and checks that each
group holds exactly one distinct total_pnl. If a group holds two, the rule would
have merged two genuinely different strategies — the run aborts and the rule is
wrong.

    docker compose exec -T worker-optimize python -m tools.combo_dedup_verify <job_id_prefix>

Exit 0 = safe to dedup. Exit 1 = unsafe, do not enable.
"""
import sys
import collections

sys.path.insert(0, "/app")

from services.optimizer import result_store as rs                    # noqa: E402
from services.optimizer.combo_dedup import effective_fingerprint     # noqa: E402
from services.optimizer.param_expander import apply_combo_for_optim  # noqa: E402


def main(prefix: str) -> int:
    import redis, os, json
    r = redis.Redis(host=os.getenv("REDIS_HOST", "redis"))
    keys = [k.decode().split(":")[1] for k in r.scan_iter(f"optim:{prefix}*:meta")]
    if not keys:
        print(f"no job matching {prefix!r}")
        return 2
    job = keys[0]
    meta = json.loads(r.get(f"optim:{job}:meta"))
    base = meta.get("base_payload") or {}
    rows = rs.get_all_results_raw(job)
    print(f"job {job[:8]}  rows={len(rows)}")

    groups = collections.defaultdict(list)
    for row in rows:
        merged = apply_combo_for_optim(base, row.get("combo") or {})
        groups[effective_fingerprint(merged)].append(row)

    bad = 0
    for fp, members in groups.items():
        pnls = {round(float((m.get("summary") or {}).get("total_pnl") or 0.0), 4)
                for m in members}
        if len(pnls) > 1:
            bad += 1
            if bad <= 5:
                print(f"  UNSAFE {fp[:12]}  {len(members)} combos, {len(pnls)} different P&L: "
                      f"{sorted(pnls)[:4]}")
                for m in members[:2]:
                    print(f"     {m.get('combo_label','')[:80]}")

    uniq = len(groups)
    print(f"\nfingerprint groups : {uniq}")
    print(f"would skip         : {len(rows) - uniq}  ({100*(1-uniq/max(len(rows),1)):.0f}% of the run)")
    print(f"groups with mixed P&L: {bad}")
    if bad:
        print("\nRESULT: UNSAFE — the rule merges strategies that differ. Do NOT enable.")
        return 1
    print("\nRESULT: SAFE — every merged group has one identical result.")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1] if len(sys.argv) > 1 else ""))
