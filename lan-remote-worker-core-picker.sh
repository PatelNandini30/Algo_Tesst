#!/usr/bin/env bash
# lan-remote-worker-core-picker.sh
#
# Rebuild the LAN remote-worker image and publish the tar to BOTH:
#   - local : remote-worker/algotest-worker-image.tar
#   - share : the SMB folder the worker PCs' watch-update.bat polls
#
# Run this after editing backend code. Each guard below corresponds to a real
# failure we hit: jobs killed mid-run, stale Rust .so shipped, image != live
# because source moved mid-build, share not mounted, corrupt copy.
#
# Requires sudo (Docker artifacts are root-owned) — you'll be prompted once.
# Rust source changes still need `sudo ./start.sh` first (that recompiles the
# wheel); this script refuses to ship a stale .so.

set -euo pipefail
cd "$(dirname "$0")"

IMAGE="algotest-backend-app:latest"
LOCAL_TAR="remote-worker/algotest-worker-image.tar"
SHARE_TAR="/run/user/$(id -u)/gvfs/smb-share:server=192.168.4.50,share=share/Manan Pujara/Algo Test/remote-worker/algotest-worker-image.tar"

# The calc-path env vars code_version.py fingerprints — MUST match the running
# worker or the env-matched check below compares apples to oranges.
WENV=(-e MIXED_FUT_RUST=1 -e FAST_LOOKUP_MODE=rust -e ENGINE_BACKEND=rust -e OPTIMIZE_RUST_XLSX=1 -e BACKTEST_INCLUDE_MAE_MFE=1)

say() { printf '\n=== %s ===\n' "$*"; }
die() { echo "ABORT: $*" >&2; exit 1; }

say "Checking for running jobs"
bt=$(sudo docker exec algotest-redis redis-cli LLEN backtests 2>/dev/null || echo 0)
bf=$(sudo docker exec algotest-redis redis-cli LLEN backtests_fast 2>/dev/null || echo 0)
ao=$(sudo docker exec algotest-redis redis-cli KEYS 'algotest:active_optim*' 2>/dev/null || true)
[ "${bt:-0}" = 0 ] && [ "${bf:-0}" = 0 ] && [ -z "$ao" ] \
  || die "jobs in flight (backtests=$bt fast=$bf optim='$ao') — a rebuild would kill them. Try later."

say "Checking Rust .so freshness"
rhash=$( { find backend/native/src -name '*.rs' | sort | xargs md5sum; \
           md5sum backend/native/Cargo.toml backend/native/Cargo.lock; } 2>/dev/null | md5sum | cut -d' ' -f1)
stored=$(cat .rust_wheel_hash 2>/dev/null || echo "")
[ "$rhash" = "$stored" ] \
  || die "Rust source changed ($rhash != $stored). A plain image build does NOT recompile Rust — run 'sudo ./start.sh' first."

say "Building backend image"
sudo docker compose build backend

say "Verifying image matches the live worker"
img=$(sudo docker run --rm -w /app "${WENV[@]}" --entrypoint python "$IMAGE" \
        -c 'from services.code_version import compute_code_version as g; print(g())')
live=$(sudo docker exec -w /app algotest-worker-backtests python \
        -c 'from services.code_version import compute_code_version as g; print(g())')
iso=$(sudo docker run --rm --entrypoint sh "$IMAGE" \
        -c 'sha256sum $(python -c "import algotest_native,os;print(os.path.dirname(algotest_native.__file__))")/algotest_native.abi3.so' | awk '{print $1}')
tmp=$(mktemp -d); cp "$(ls -t backend/prebuilt/*.whl | head -1)" "$tmp/w.zip"
( cd "$tmp" && unzip -oq w.zip ); wso=$(find "$tmp" -name '*.so' | xargs sha256sum | awk '{print $1}'); rm -rf "$tmp"
echo "  image=$img  live=$live"
echo "  imgso=$iso"
echo "  whlso=$wso"
[ "$img" = "$live" ] || die "image ($img) != live worker ($live) — source moved mid-build. Re-run."
[ "$iso" = "$wso" ]  || die "image .so != wheel .so — base image is stale. Run 'sudo ./start.sh'."

say "Saving local tar"
sudo docker save "$IMAGE" -o "$LOCAL_TAR"
sudo chown "$(id -u):$(id -g)" "$LOCAL_TAR"
lsha=$(sha256sum "$LOCAL_TAR" | awk '{print $1}')

say "Publishing to share"
sharedir=$(dirname "$SHARE_TAR")
[ -d "$sharedir" ] || die "share not mounted at: $sharedir"$'\n'"Open it once in Files (Nautilus), then re-run."
cp "$LOCAL_TAR" "$SHARE_TAR"
ssha=$(sha256sum "$SHARE_TAR" | awk '{print $1}')
[ "$lsha" = "$ssha" ] || die "share copy hash mismatch ($lsha != $ssha)."

say "DONE"
echo "code_version : $img"
echo "tar sha256   : $lsha"
echo "size         : $(stat -c %s "$LOCAL_TAR") bytes"
echo "local        : $LOCAL_TAR"
echo "share        : $SHARE_TAR"
echo
echo "Remote nodes load it via watch-update.bat within ~60s. No backend restart"
echo "needed — the main box recomputes its staleness baseline live."
