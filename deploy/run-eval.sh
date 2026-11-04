#!/usr/bin/env bash
set -euo pipefail
umask 077
base=/root/zt_dir/ai_base/DuckKing_ux_review
mkdir -p "$base/logs" "$base/artifacts/agent" "$base/state"
exec 9>"$base/eval.lock"
flock -n 9 || { echo 'Another evaluation is already running.' >&2; exit 2; }
cd "$base/current"
log="$base/logs/eval-$(date -u +%Y%m%dT%H%M%SZ).log"
ln -sfn "$(basename "$log")" "$base/logs/latest-eval.log"
export PYTHONUNBUFFERED=1
export PLAYWRIGHT_BROWSERS_PATH="$base/browser-cache"
.venv/bin/python - "$base/state/evaluation.json" <<'PY_START'
import datetime, json, pathlib, sys
path = pathlib.Path(sys.argv[1])
data = {"status":"running", "started_at":datetime.datetime.now(datetime.timezone.utc).isoformat(), "version":json.loads(pathlib.Path("release.json").read_text())["version"]}
temp = path.with_suffix(".tmp")
temp.write_text(json.dumps(data))
temp.replace(path)
PY_START
set +e
.venv/bin/ai-ux --config configs/jfcs62.yaml eval "$@" 2>&1 | tee "$log"
rc=${PIPESTATUS[0]}
set -e
printf '%s\n' "$rc" > "${log%.log}.exit"
.venv/bin/python - "$base/state/evaluation.json" "$rc" <<'PY_END'
import datetime, json, pathlib, sys
path = pathlib.Path(sys.argv[1])
data = json.loads(path.read_text())
data.update(status="completed" if sys.argv[2]=="0" else "failed", exit_code=int(sys.argv[2]), finished_at=datetime.datetime.now(datetime.timezone.utc).isoformat())
temp = path.with_suffix(".tmp")
temp.write_text(json.dumps(data))
temp.replace(path)
PY_END
exit "$rc"
