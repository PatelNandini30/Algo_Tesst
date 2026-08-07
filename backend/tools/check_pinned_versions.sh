#!/usr/bin/env sh
# Verify every exact pin in requirements.txt still exists on PyPI for THIS
# interpreter/platform.
#
# Why this exists: granian==1.6.0 was pulled from the index, but the pip layer
# was cached so nothing re-resolved it. The failure only appeared months later,
# when a Rust-wheel change invalidated that layer and `sudo ./start.sh` died
# mid-rebuild with "No matching distribution found" — at the worst moment, with
# the stack already down.
#
# Run inside the built base image so the interpreter, platform and the
# SonicWALL trusted-host flags match a real build:
#
#   docker run --rm -v "$PWD/backend/requirements.txt:/req.txt:ro" \
#     algo-backend-base:latest sh /req_check.sh
#
# ...or from the repo root:
#   docker run --rm -v "$PWD/backend:/b:ro" algo-backend-base:latest \
#     sh /b/tools/check_pinned_versions.sh /b/requirements.txt
#
# Exit 0 = every pin resolvable. Exit 1 = at least one is gone.
REQ="${1:-/req.txt}"
PIPF="--trusted-host pypi.org --trusted-host files.pythonhosted.org --trusted-host pypi.python.org --disable-pip-version-check"
rc=0

grep -v '^#' "$REQ" | grep -v '^$' | sed 's/#.*//' | sed 's/;.*//' | tr -d ' ' | while read -r spec; do
    [ -z "$spec" ] && continue
    case "$spec" in *==*) ;; *) continue ;; esac   # only exact pins
    pkg=${spec%%==*}
    ver=${spec##*==}
    avail=$(pip index versions "$pkg" $PIPF 2>/dev/null | sed -n 's/^Available versions: //p')
    if [ -z "$avail" ]; then
        # Distinguish "index unreachable" from "version gone" — treat as failure
        # either way, but say which, so a network blip is not read as a yank.
        echo "?? $pkg==$ver  (index query failed — network/cert issue?)"
        echo fail > /tmp/.pin_rc
        continue
    fi
    if echo "$avail" | tr -d ' ' | tr ',' '\n' | grep -qx "$ver"; then
        echo "OK      $pkg==$ver"
    else
        echo "GONE    $pkg==$ver  (available: $(echo "$avail" | cut -c1-60)...)"
        echo fail > /tmp/.pin_rc
    fi
done

[ -f /tmp/.pin_rc ] && rc=1
rm -f /tmp/.pin_rc
exit $rc
