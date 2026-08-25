# Completion Matrix

Nothing in this matrix is considered complete without authoritative test or
parity evidence. `TODO` is deliberately explicit so partial scaffolding cannot
be mistaken for delivery.

| Area | Requirement | State | Required evidence |
|---|---|---|---|
| Isolation | No live file/service/port/write-path changes | VERIFIED TO DATE | hash audit + isolated Compose inspection |
| Domain | Parse every current frontend/API strategy field | IN PROGRESS | captured-request corpus parses losslessly |
| Market data | Options/spot/futures/index OHLC lookup parity | IN PROGRESS | snapshot lookup diff: zero mismatches; multi-symbol cache verified; live-Postgres loader CSV==PG clean (NIFTY 2023–24, MIDCPNIFTY), runtime column resolution; warm single-entry market cache (cold 2.58s→warm 0.05s, identical results, version-guarded + OOM-safe) |
| Engine | Weekly/monthly/basic legs | IN PROGRESS | weekly, monthly, futures and next-weekly fixtures clean |
| Engine | All strike modes | IN PROGRESS | native ATM/premium/time-value/relative/delta resolvers; dedicated mode corpus pending |
| Engine | SL/target/trail/buffer/overall exits | IN PROGRESS | per-leg + option, underlying, and futures overall implemented; wider parity pending |
| Engine | filters/rollover/yearly/mixed/next expiry | IN PROGRESS | weekly/monthly/yearly and individual-filter split acceptance clean; folder-based filter_date_sets keys resolved dynamically from live DB (gap-month exclusion verified); full corpus pending |
| Engine | futures/multi-index/per-leg filter | IN PROGRESS | multi-index acceptance is order-invariant; full synchronized-risk parity pending |
| Engine | spot adjustment/re-entry/lazy/Midcap | IN PROGRESS | spot, ASAP/reverse, momentum, lazy and native Midcap overlay verified; full field parity pending |
| Analytics | MAE/MFE/DD/CAGR/all summaries | IN PROGRESS | core metrics + leg-order invariance; full key/patchwise parity pending |
| Optimizer | exhaustive/random/smart expansion | IN PROGRESS | all three native; smart algorithm/output parity pending |
| Optimizer | native parallel batch and shared work | IN PROGRESS | strict two-pass 30k/30k, zero failures, 0.837s release/covered corpus |
| Artifacts | CSV/XLSX/WOW-MOM/ZIP/rules | IN PROGRESS | bounded summary, tradesheet, ZIP, WOW/MOM artifacts valid; exact layout parity pending |
| Jobs | queue/leases/progress/cancel/resume/watchdog | IN PROGRESS | durable queue/list/progress/cancel/restart watchdog, paginated all-failure diagnostics, deterministic exhaustive/random and zero-result smart resume; distributed leases pending |
| API | existing request/response compatibility | IN PROGRESS | health/version/preview/backtest/jobs/results/list/resume and native artifact routes implemented; /api/backtest returns frontend-shaped {trades(Title-Case + Leg-1 equity curve), summary(flat)} verified vs live DB; exact Python Cumulative-convention parity pending |
| Cutover | shadow/canary/rollback | IN PROGRESS | isolated localhost canary/stop drill clean; production cutover deliberately not performed |

Global completion requires zero valid combinations failing, no Python runtime,
and no unexplained parity difference.
