from __future__ import annotations

import json

from app.harness.artifacts import ArtifactStore
from app.intake.step6e4 import refresh_step6e4_artifacts

TASK_ID = "task_step6e3_zhipu_tencent_meeting_pilot"


def main() -> None:
    result = refresh_step6e4_artifacts(TASK_ID, store=ArtifactStore())
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
