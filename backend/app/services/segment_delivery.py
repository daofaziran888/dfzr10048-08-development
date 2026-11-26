"""分段交付领域服务：按收件方与片段校验决定待发范围，驱动 待生成→已领取→已签收/已抵扣 流转。

- 局部补发：为（字幕包, 接收方）生成/刷新分段交付计划，旧签收只抵扣同收件方、
  相同区间且内容校验和一致的片段；改过的行必须送达。
- 签收台账由交付段记录实时重建，同一区间内容对同一收件方只保留一次有效凭据，
  重送签收不重复计行；迟到签收会反向抵扣其它字幕包的同内容待发段。
- 交付回执的交付行数与签收结论由分段状态汇总回写。
"""
from __future__ import annotations

from datetime import date
from typing import Any

from app.pipeline.segment_delivery import (
    SegmentDeliveryLedger,
    segment_checksum,
    split_segments,
)
from app.services.base import BaseModuleService
from app.store import store

TERMINAL_STATUSES = ("已签收", "已抵扣")


class SegmentDeliveryService(BaseModuleService):
    MODULE = "segment_delivery"
    ENTITY = "交付段"
    OPERATION_TERM = "交付"
    LIST_FIELDS = ["交付段编号", "关联字幕包", "接收方标识", "片段区间", "内容校验和", "已送至行号", "抵扣来源", "签收凭据", "重复签收"]
    REQUIRED_FIELDS = ["交付段编号", "关联字幕包", "接收方标识"]
    STATUS_ORDER = ["待生成", "已领取", "已签收", "已抵扣"]
    ACTION_RULES = {"领取片段": "已领取", "确认签收": "已签收"}
    NEGATIVE_ACTIONS = []

    # ---------- 基础查询 ----------

    @staticmethod
    def _pack_row(pack_code: str) -> dict[str, Any] | None:
        for row in store.rows("subtitle_pack"):
            if row.get("字幕包编号") == pack_code:
                return row
        return None

    @staticmethod
    def _receipt_row(pack_code: str, recipient: str) -> dict[str, Any] | None:
        for row in store.rows("delivery_receipt"):
            if row.get("关联字幕包") == pack_code and row.get("接收方标识") == recipient:
                return row
        return None

    @classmethod
    def _receipt_code(cls, pack_code: str, recipient: str) -> str:
        receipt = cls._receipt_row(pack_code, recipient)
        return str(receipt.get("回执编号")) if receipt else pack_code

    @staticmethod
    def _row_span(row: dict[str, Any]) -> tuple[int, int]:
        """片段起止行：优先取隐藏字段，兜底解析展示字段「片段区间」。"""
        start = int(row.get("_start") or 0)
        end = int(row.get("_end") or 0)
        if start and end:
            return start, end
        text = str(row.get("片段区间") or "")
        if "-" in text:
            head, tail = text.split("-", 1)
            if head.strip().isdigit() and tail.strip().isdigit():
                return int(head), int(tail)
        return 0, 0

    def _rows_for(self, pack_code: str, recipient: str) -> list[dict[str, Any]]:
        rows = [
            row for row in store.rows(self.MODULE)
            if row.get("关联字幕包") == pack_code and row.get("接收方标识") == recipient
        ]
        rows.sort(key=lambda row: self._row_span(row))
        return rows

    @staticmethod
    def _segments_of(pack: dict[str, Any]) -> list[dict[str, Any]]:
        lines = list(pack.get("_lines") or [])
        segments = []
        for index, (start, end) in enumerate(split_segments(len(lines)), start=1):
            segments.append({
                "seg_no": index, "start": start, "end": end,
                "checksum": segment_checksum(lines, start, end),
            })
        return segments

    def _ledger(self) -> SegmentDeliveryLedger:
        """从交付段记录重建签收台账：同一（收件方, 区间, 校验和）只有一份有效凭据。"""
        ledger = SegmentDeliveryLedger()
        for row in store.rows(self.MODULE):
            voucher = str(row.get("签收凭据") or "-")
            start, end = self._row_span(row)
            if voucher in ("-", "") or not start:
                continue
            credit_source = str(row.get("抵扣来源") or "-")
            receipt = credit_source if credit_source != "-" else self._receipt_code(
                str(row.get("关联字幕包")), str(row.get("接收方标识")))
            ledger.register(
                str(row.get("接收方标识")), start, end, str(row.get("内容校验和")),
                voucher, str(row.get("关联字幕包")), receipt,
            )
        return ledger

    def _next_voucher(self, pack_code: str, seg_no: int) -> str:
        used = sum(
            1 for row in store.rows(self.MODULE)
            if str(row.get("签收凭据") or "-") not in ("-", "")
        )
        return f"SGV-{pack_code}-S{seg_no}-{used + 1:04d}"

    # ---------- 计划生成（局部补发入口） ----------

    def _new_row(
        self, pack_code: str, recipient: str,
        seg_no: int, start: int, end: int, checksum: str,
    ) -> dict[str, Any]:
        rows = store.rows(self.MODULE)
        serial = max(
            (int(str(row.get("交付段编号", "SEGD-0")).split("-")[-1]) for row in rows
             if str(row.get("交付段编号", "")).split("-")[-1].isdigit()),
            default=0,
        ) + 1
        entry = {
            "id": max((int(row.get("id", 0)) for row in rows), default=0) + 1,
            "交付段编号": f"SEGD-{serial:04d}",
            "关联字幕包": pack_code, "接收方标识": recipient,
            "片段区间": f"{start}-{end}", "内容校验和": checksum,
            "已送至行号": "0", "抵扣来源": "-", "签收凭据": "-", "重复签收": "否",
            "status": "待生成", "pending": True, "abnormal": False,
            "_seg_no": seg_no, "_start": start, "_end": end, "_credited": False,
        }
        rows.append(entry)
        return entry

    @staticmethod
    def _apply_credit(row: dict[str, Any], record: dict[str, str]) -> None:
        """用既有签收凭据抵扣片段：旧签收仅在同一收件方、内容相同的区间生效。"""
        row["status"] = "已抵扣"
        row["pending"] = False
        row["签收凭据"] = record["voucher"]
        row["抵扣来源"] = record["receipt"]
        row["_credited"] = True

    def generate_plan(self, pack_code: str, recipient: str) -> dict[str, Any] | None:
        """生成/刷新（字幕包, 接收方）的分段交付计划，由服务端校验决定待发范围。"""
        pack = self._pack_row(pack_code)
        if pack is None:
            return None
        ledger = self._ledger()
        existing = {self._row_span(row): row for row in self._rows_for(pack_code, recipient)}
        for seg in self._segments_of(pack):
            record = ledger.lookup(recipient, seg["start"], seg["end"], seg["checksum"])
            row = existing.get((seg["start"], seg["end"]))
            if row is None:
                row = self._new_row(
                    pack_code, recipient, seg["seg_no"],
                    seg["start"], seg["end"], seg["checksum"],
                )
            if record is not None and row.get("status") in ("待生成", "已领取"):
                self._apply_credit(row, record)
        self._rollup_receipt(pack_code, recipient)
        return self.plan_summary(pack_code, recipient)

    # ---------- 状态流转 ----------

    def _find_row(
        self, pack_code: str, recipient: str, seg_no: int,
    ) -> dict[str, Any] | None:
        pack = self._pack_row(pack_code)
        if pack is None:
            return None
        segments = self._segments_of(pack)
        if seg_no < 1 or seg_no > len(segments):
            return None
        span = (segments[seg_no - 1]["start"], segments[seg_no - 1]["end"])
        for row in self._rows_for(pack_code, recipient):
            if self._row_span(row) == span:
                return row
        return None

    def claim_segment(
        self, pack_code: str, recipient: str, seg_no: int,
    ) -> tuple[bool, str, dict[str, Any] | None]:
        """领取片段：待生成 → 已领取。"""
        row = self._find_row(pack_code, recipient, seg_no)
        if row is None:
            return False, f"片段 {seg_no} 不存在，请先生成待发段（局部补发）", None
        status = row.get("status")
        if status == "待生成":
            row["status"] = "已领取"
            row["pending"] = True
            return True, f"片段 {row['片段区间']} 已领取，可下载实际剩余字幕文件", self.plan_summary(pack_code, recipient)
        if status == "已抵扣":
            return False, f"片段 {row['片段区间']} 已由 {row['抵扣来源']} 抵扣，无需领取；如仍需重送可直接登记送达", None
        if status == "已领取":
            return False, f"片段 {row['片段区间']} 已领取", None
        return False, f"片段 {row['片段区间']} 已签收，不能重复领取", None

    def record_progress(
        self, pack_code: str, recipient: str, upto_line: int,
    ) -> tuple[bool, str, dict[str, Any] | None]:
        """登记送达位置：把目标行号写入所属片段的「已送至行号」。"""
        pack = self._pack_row(pack_code)
        if pack is None:
            return False, f"字幕包 {pack_code} 不存在", None
        rows = self._rows_for(pack_code, recipient)
        if not rows:
            return False, "请先生成待发段（局部补发）", None
        total = int(pack.get("字幕行数", "0") or 0)
        if upto_line < 1 or upto_line > total:
            return False, f"行号 {upto_line} 超出字幕包范围（1-{total}）", None
        target = None
        for row in rows:
            start, end = self._row_span(row)
            if start <= upto_line <= end:
                target = row
                break
        if target is None:
            return False, f"第 {upto_line} 行不属于任何交付段", None
        if target.get("status") not in ("已领取", "已抵扣"):
            return False, f"第 {upto_line} 行所属片段 {target['片段区间']} 未领取，不能登记送达", None
        _, end = self._row_span(target)
        current = int(target.get("已送至行号", "0") or 0)
        if upto_line <= current:
            return False, f"第 {upto_line} 行此前已送达（当前已送至第 {current} 行）", None
        if upto_line > end:
            return False, f"第 {upto_line} 行超出片段 {target['片段区间']} 范围", None
        target["已送至行号"] = str(upto_line)
        return True, f"已登记送达至第 {upto_line} 行", self.plan_summary(pack_code, recipient)

    def sign_segment(
        self, pack_code: str, recipient: str, seg_no: int,
    ) -> tuple[bool, str, dict[str, Any] | None]:
        """签收片段：已领取（送完）→ 已签收；重送签收不重复计行，并反向抵扣同内容待发段。"""
        row = self._find_row(pack_code, recipient, seg_no)
        if row is None:
            return False, f"片段 {seg_no} 不存在，请先生成待发段（局部补发）", None
        status = row.get("status")
        if status == "已签收":
            return False, f"片段 {row['片段区间']} 已签收，重复签收不计行", None
        if status == "待生成":
            return False, f"片段 {row['片段区间']} 未领取，不能签收", None
        start, end = self._row_span(row)
        if status == "已领取" and int(row.get("已送至行号", "0") or 0) < end:
            return False, f"片段 {row['片段区间']} 尚未送完（已送至第 {row['已送至行号']} 行），不能签收", None

        ledger = self._ledger()
        candidate = self._next_voucher(pack_code, seg_no)
        record, duplicate = ledger.register(
            recipient, start, end, str(row.get("内容校验和")),
            candidate, pack_code, self._receipt_code(pack_code, recipient),
        )
        row["签收凭据"] = record["voucher"]
        row["重复签收"] = "是" if duplicate else "否"
        row["status"] = "已签收"
        row["pending"] = False

        affected_packs = {pack_code}
        if duplicate:
            message = f"片段 {row['片段区间']} 重复签收：该区间内容已有有效凭据 {record['voucher']}，不重复计行"
        else:
            message = f"片段 {row['片段区间']} 已签收，凭据 {record['voucher']}"
            # 迟到签收反向抵扣：同一收件方、相同区间且内容一致的其它待发段直接抵扣
            for other in store.rows(self.MODULE):
                if other is row or other.get("接收方标识") != recipient:
                    continue
                if other.get("status") not in ("待生成", "已领取"):
                    continue
                o_start, o_end = self._row_span(other)
                if (o_start, o_end) == (start, end) and other.get("内容校验和") == row.get("内容校验和"):
                    self._apply_credit(other, record)
                    affected_packs.add(str(other.get("关联字幕包")))
        for affected in affected_packs:
            self._rollup_receipt(affected, recipient)
        return True, message, self.plan_summary(pack_code, recipient)

    # ---------- 汇总与查询 ----------

    def _rollup_receipt(self, pack_code: str, recipient: str) -> None:
        """把分段状态汇总回写交付回执：交付行数只计有效签收与抵扣区间，不重复计行。"""
        receipt = self._receipt_row(pack_code, recipient)
        if receipt is None:
            return
        rows = self._rows_for(pack_code, recipient)
        if not rows:
            return
        effective = sum(
            end - start + 1
            for row in rows if row.get("status") in TERMINAL_STATUSES
            for start, end in [self._row_span(row)]
        )
        receipt["交付行数"] = str(effective)
        terminal = sum(1 for row in rows if row.get("status") in TERMINAL_STATUSES)
        if terminal == len(rows):
            receipt["status"] = "已签收"
            receipt["交付结论"] = "签收"
            receipt["交付时间"] = date.today().isoformat()
            receipt["pending"] = False
        elif terminal > 0:
            receipt["status"] = "部分签收"
            receipt["交付结论"] = "部分签收"
            receipt["pending"] = True
        else:
            receipt["status"] = "交付中"
            receipt["交付结论"] = "-"
            receipt["pending"] = True

    def plan_summary(self, pack_code: str, recipient: str) -> dict[str, Any] | None:
        """发送页视图：已发区间、抵扣区间、待发区间与实际停止位置。"""
        pack = self._pack_row(pack_code)
        if pack is None:
            return None
        segments: list[dict[str, Any]] = []
        sent_ranges: list[list[int]] = []
        credited_ranges: list[list[int]] = []
        pending_ranges: list[list[int]] = []
        stop_line = 0
        remaining = 0
        for row in self._rows_for(pack_code, recipient):
            start, end = self._row_span(row)
            sent_upto = int(row.get("已送至行号", "0") or 0)
            stop_line = max(stop_line, sent_upto)
            status = str(row.get("status"))
            segments.append({
                "id": row.get("id"),
                "seg_no": row.get("_seg_no"),
                "range": row.get("片段区间"),
                "start": start, "end": end,
                "checksum": row.get("内容校验和"),
                "status": status,
                "sent_upto": sent_upto,
                "credit_source": row.get("抵扣来源"),
                "voucher": row.get("签收凭据"),
                "duplicate": row.get("重复签收"),
            })
            if sent_upto > 0:
                sent_ranges.append([start, min(sent_upto, end)])
            if status == "已抵扣" or row.get("_credited"):
                credited_ranges.append([start, end])
            elif status in ("待生成", "已领取"):
                low = max(start, sent_upto + 1) if sent_upto >= start else start
                if low <= end:
                    pending_ranges.append([low, end])
                    remaining += end - low + 1
        receipt = self._receipt_row(pack_code, recipient)
        return {
            "pack": pack_code,
            "recipient": recipient,
            "line_count": int(pack.get("字幕行数", "0") or 0),
            "segments": segments,
            "sent_ranges": sent_ranges,
            "credited_ranges": credited_ranges,
            "pending_ranges": pending_ranges,
            "stop_line": stop_line,
            "remaining_count": remaining,
            "receipt": None if receipt is None else {
                "回执编号": receipt.get("回执编号"),
                "status": receipt.get("status"),
                "交付行数": receipt.get("交付行数"),
                "重交次数": receipt.get("重交次数"),
                "交付结论": receipt.get("交付结论"),
            },
        }

    def lookup_voucher(self, voucher: str) -> list[dict[str, Any]]:
        """凭据复查：按签收凭据找回全部关联交付段（含抵扣段），刷新后结果一致。"""
        voucher = voucher.strip()
        if not voucher:
            return []
        records = []
        for row in self._rows_all():
            if str(row.get("签收凭据") or "-") == voucher:
                records.append({
                    "交付段编号": row.get("交付段编号"),
                    "关联字幕包": row.get("关联字幕包"),
                    "接收方标识": row.get("接收方标识"),
                    "片段区间": row.get("片段区间"),
                    "status": row.get("status"),
                    "已送至行号": row.get("已送至行号"),
                    "抵扣来源": row.get("抵扣来源"),
                    "签收凭据": row.get("签收凭据"),
                    "重复签收": row.get("重复签收"),
                })
        return records

    def _rows_all(self) -> list[dict[str, Any]]:
        return list(store.rows(self.MODULE))

    def remaining_lines(self, pack_code: str, recipient: str) -> tuple[str, str, int] | None:
        """实际的剩余字幕文件：待发片段中尚未送出的字幕行。"""
        pack = self._pack_row(pack_code)
        if pack is None:
            return None
        lines = list(pack.get("_lines") or [])
        output: list[str] = []
        for row in self._rows_for(pack_code, recipient):
            if row.get("status") not in ("待生成", "已领取"):
                continue
            start, end = self._row_span(row)
            sent_upto = int(row.get("已送至行号", "0") or 0)
            low = max(start, sent_upto + 1) if sent_upto >= start else start
            for number in range(low, end + 1):
                if 1 <= number <= len(lines):
                    output.append(f"{number}\t{lines[number - 1]}")
        filename = f"{pack_code}-remaining.txt"
        return filename, "\n".join(output), len(output)

    # ---------- 字幕包页动作 ----------

    def claim_remaining_for_pack(self, pack_code: str) -> tuple[bool, str]:
        """字幕包页「领取剩余文件」：为全部接收方领取仍待生成的片段。"""
        pack = self._pack_row(pack_code)
        if pack is None:
            return False, f"字幕包 {pack_code} 不存在"
        receipts = [
            row for row in store.rows("delivery_receipt")
            if row.get("关联字幕包") == pack_code
        ]
        if not receipts:
            return False, f"字幕包 {pack_code} 暂无交付回执，无法领取剩余文件"
        claimed_segments = 0
        claimed_lines = 0
        claimed_recipients = 0
        for receipt in receipts:
            recipient = str(receipt.get("接收方标识"))
            self.generate_plan(pack_code, recipient)
            before = claimed_segments
            for row in self._rows_for(pack_code, recipient):
                if row.get("status") == "待生成":
                    row["status"] = "已领取"
                    row["pending"] = True
                    start, end = self._row_span(row)
                    claimed_segments += 1
                    claimed_lines += end - start + 1
            if claimed_segments > before:
                claimed_recipients += 1
        if claimed_segments == 0:
            return False, f"字幕包 {pack_code} 没有待领取的剩余片段"
        return True, f"已为 {claimed_recipients} 个接收方领取剩余 {claimed_segments} 段（共 {claimed_lines} 行）"

    def close_pack(self, pack_code: str) -> tuple[bool, str]:
        """字幕包页「办结」：全部接收方签收后字幕包办结。"""
        pack = self._pack_row(pack_code)
        if pack is None:
            return False, f"字幕包 {pack_code} 不存在"
        receipts = [
            row for row in store.rows("delivery_receipt")
            if row.get("关联字幕包") == pack_code
        ]
        if not receipts:
            return False, f"字幕包 {pack_code} 暂无交付回执，不能办结"
        unsigned = [str(row.get("回执编号")) for row in receipts if row.get("status") != "已签收"]
        if unsigned:
            return False, f"仍有接收方未签收（{'、'.join(unsigned)}），不能办结"
        pack["status"] = "已办结"
        pack["pending"] = False
        return True, f"字幕包 {pack_code} 全部接收方已签收，办结完成"

    # ---------- 通用动作入口 ----------

    def run_action(self, entry_id: int, action: str) -> tuple[dict[str, Any] | None, str]:
        row = store.find(self.MODULE, entry_id)
        if row is None:
            return None, f"{self.ENTITY} {entry_id} 不存在或已归档"
        pack_code = str(row.get("关联字幕包"))
        recipient = str(row.get("接收方标识"))
        seg_no = int(row.get("_seg_no") or 0)
        if action == "领取片段":
            ok, message, _ = self.claim_segment(pack_code, recipient, seg_no)
            return (row if ok else None), message
        if action == "确认签收":
            ok, message, _ = self.sign_segment(pack_code, recipient, seg_no)
            return (row if ok else None), message
        return super().run_action(entry_id, action)
