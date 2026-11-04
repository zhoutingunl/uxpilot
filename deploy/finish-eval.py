"""Finalize interrupted systemd batches without relabeling completed results."""

import datetime
import json
import os
from pathlib import Path

path = Path("/root/zt_dir/ai_base/DuckKing_ux_review/state/evaluation.json")
if path.exists():
    data = json.loads(path.read_text())
    if data.get("status") == "running":
        value = os.environ.get("EXIT_STATUS", "")
        data.update(
            status="failed",
            finished_at=datetime.datetime.now(datetime.timezone.utc).isoformat(),
            exit_code=int(value) if value.isdigit() else None,
            service_result=os.environ.get("SERVICE_RESULT", "interrupted"),
        )
        temp = path.with_suffix(".tmp")
        temp.write_text(json.dumps(data))
        temp.replace(path)
