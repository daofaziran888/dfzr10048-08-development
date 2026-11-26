"""交付回执写入领域服务：记录字幕包交付接收方的行数与签收结论，产出回执记录数据集供归档架位与审计核查消费。"""
from __future__ import annotations

from typing import Any

from app.services.base import BaseModuleService
from app.store import store


class DeliveryReceiptService(BaseModuleService):
    MODULE = "delivery_receipt"
    ENTITY = "回执记录"
    OPERATION_TERM = "写入"
    LIST_FIELDS = ["回执编号", "关联字幕包", "接收方标识", "交付行数", "交付格式", "交付结论", "重交次数", "交付时间"]
    REQUIRED_FIELDS = ["回执编号", "关联字幕包", "接收方标识"]
    STATUS_ORDER = ["待交付", "交付中", "已签收", "部分签收"]
    ACTION_RULES = {"启动交付": "交付中", "确认签收": "已签收", "登记部分签收": "部分签收"}
    NEGATIVE_ACTIONS = []

    def run_action(self, entry_id: int, action: str) -> tuple[dict[str, Any] | None, str]:
        if action == "局部补发":
            return self._partial_redeliver(entry_id)
        return super().run_action(entry_id, action)

    def _partial_redeliver(self, entry_id: int) -> tuple[dict[str, Any] | None, str]:
        """局部补发入口：为（字幕包, 接收方）生成/刷新分段交付计划。

        待发范围由服务端按收件方及片段校验决定：旧签收抵扣同内容区间，
        改过的行仍进入待发；重送签收不重复计行。
        """
        entry = store.find(self.MODULE, entry_id)
        if entry is None:
            return None, f"{self.ENTITY} {entry_id} 不存在或已归档"
        pack_code = str(entry.get("关联字幕包"))
        recipient = str(entry.get("接收方标识"))
        from app.services import service_for
        service = service_for("segment_delivery")
        if service is None:
            return None, "分段交付服务未注册"
        summary = service.generate_plan(pack_code, recipient)  # type: ignore[attr-defined]
        if summary is None:
            return None, f"字幕包 {pack_code} 不存在，无法生成局部补发计划"
        entry["重交次数"] = str(int(entry.get("重交次数", "0") or 0) + 1)
        # 回执状态与交付行数由 generate_plan 内部汇总回写，这里只补一句可读说明。
        def fmt(ranges: list[list[int]]) -> str:
            return "、".join(f"{low}-{high}" for low, high in ranges) or "无"
        return entry, (
            f"局部补发计划已生成：待发 {fmt(summary['pending_ranges'])}，"
            f"抵扣 {fmt(summary['credited_ranges'])}，已发 {fmt(summary['sent_ranges'])}"
        )
