"""時間字串解析工具。

Docker Engine API 與 GitLab API 都以 ISO 8601 字串回傳時間，但兩者精度不同：

- Docker：奈秒精度（例：`2026-08-16T00:28:44.381378677Z`），
  超過 `datetime` 支援的 6 位小數。
- GitLab：毫秒精度（例：`2026-08-16T02:05:34.533Z`）或帶時區位移
  （例：`2026-08-15T10:35:05.000+00:00`）。

本模組統一把它們轉成 Unix epoch 秒數，供 Prometheus 指標使用。
"""

import re
from datetime import datetime, timezone

# 小數秒片段（最多 9 位，涵蓋 Docker 的奈秒精度）
_FRACTION_PATTERN = re.compile(r"\.\d{1,9}")

# `.` + 6 位小數 = 7 個字元，為 datetime 可接受的最大長度
_MAX_FRACTION_LENGTH = 7


def parse_iso_timestamp(value):
    """把 ISO 8601 時間字串轉成 Unix epoch 秒數。

    自動處理結尾的 `Z`（UTC）與超過 6 位的小數秒（截斷至微秒）。
    未帶時區資訊時一律視為 UTC。

    Args:
        value: ISO 8601 時間字串，允許為 None 或空字串。

    Returns:
        float: Unix epoch 秒數；無法解析時回傳 None。
    """
    if not value:
        return None

    text = str(value).strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"

    match = _FRACTION_PATTERN.search(text)
    if match and len(match.group(0)) > _MAX_FRACTION_LENGTH:
        text = (
            text[: match.start() + _MAX_FRACTION_LENGTH] + text[match.end():]
        )

    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None

    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.timestamp()
