"""分段交付计划器：按收件方与片段校验决定待发范围的纯逻辑组件。

核心行为：
- 字幕包按行数均分为三段，片段内容校验和决定新旧包区间是否一致
- 旧签收仅在同一收件方、相同区间且内容校验和一致时抵扣新包待发段
- 改过的行（校验和不同）必须送达，不产生抵扣
- 重送签收不得重复计行：同一收件方对同一区间内容只保留一次有效凭据
"""
from __future__ import annotations

import hashlib
from typing import Any

SEGMENT_PARTS = 3


def split_segments(line_count: int, parts: int = SEGMENT_PARTS) -> list[tuple[int, int]]:
    """把字幕行均分为若干段，返回 [(起始行, 结束行)]，行号 1 起、含端点。"""
    if line_count <= 0 or parts <= 0:
        return []
    base, rem = divmod(line_count, parts)
    ranges: list[tuple[int, int]] = []
    start = 1
    for index in range(parts):
        size = base + (1 if index < rem else 0)
        if size <= 0:
            continue
        ranges.append((start, start + size - 1))
        start += size
    return ranges


def default_pack_lines(media_id: str, line_count: int) -> list[str]:
    """生成媒体源字幕正文的确定性内容：同一媒体源各版本共享正文。"""
    return [f"{media_id}-第{n:03d}行-字幕正文" for n in range(1, line_count + 1)]


def derive_revision_lines(
    old_lines: list[str], edit_head: bool = True, edit_tail: bool = True,
) -> list[str]:
    """派生新版字幕行：首尾改字、中段不变。"""
    lines = list(old_lines)
    if lines and edit_head:
        lines[0] = f"{lines[0]}（首部修订）"
    if lines and edit_tail:
        lines[-1] = f"{lines[-1]}（尾部修订）"
    return lines


def segment_checksum(lines: list[str], start: int, end: int) -> str:
    """计算 [start, end]（1 起含端点）区间内容的校验和。"""
    content = "\n".join(lines[start - 1:end])
    return hashlib.md5(content.encode()).hexdigest()[:8]


class SegmentDeliveryLedger:
    """签收台账：按（收件方, 区间, 内容校验和）登记唯一有效签收凭据。

    同一 key 再次签收时返回原凭据并标记重复，保证重送签收不重复计行。
    """

    def __init__(self) -> None:
        self._records: dict[tuple[str, int, int, str], dict[str, str]] = {}

    @staticmethod
    def key(recipient: str, start: int, end: int, checksum: str) -> tuple[str, int, int, str]:
        return (recipient, start, end, checksum)

    def lookup(
        self, recipient: str, start: int, end: int, checksum: str,
    ) -> dict[str, str] | None:
        return self._records.get(self.key(recipient, start, end, checksum))

    def register(
        self, recipient: str, start: int, end: int, checksum: str,
        voucher: str, pack: str, receipt: str,
    ) -> tuple[dict[str, str], bool]:
        """登记签收凭据；key 已存在时返回原凭据记录并标记重复。"""
        key = self.key(recipient, start, end, checksum)
        existing = self._records.get(key)
        if existing is not None:
            return existing, True
        record = {"voucher": voucher, "pack": pack, "receipt": receipt}
        self._records[key] = record
        return record, False

    def snapshot(self) -> dict[str, Any]:
        return {"vouchers": len(self._records)}
