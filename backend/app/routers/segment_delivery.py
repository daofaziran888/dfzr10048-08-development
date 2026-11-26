"""分段交付专用接口：待发范围由服务端按收件方及片段校验决定。

与通用资源路由并存：本路由先注册，/api/segment_delivery 的列表、登记、
动作仍由通用资源路由处理，这里只承载计划、流转与凭据复查端点。
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel

from app.services import service_for

router = APIRouter(prefix="/api/segment_delivery", tags=["分段交付"])


def _service() -> Any:
    service = service_for("segment_delivery")
    if service is None:
        raise HTTPException(status_code=500, detail="分段交付服务未注册")
    return service


class PlanPayload(BaseModel):
    pack: str
    recipient: str


class SegmentPayload(BaseModel):
    pack: str
    recipient: str
    segment: int


class ProgressPayload(BaseModel):
    pack: str
    recipient: str
    upto_line: int


@router.post("/plan")
def create_plan(payload: PlanPayload) -> dict[str, Any]:
    """生成/刷新局部补发计划：按收件方与片段校验决定待发范围。"""
    service = _service()
    summary = service.generate_plan(payload.pack.strip(), payload.recipient.strip())
    if summary is None:
        raise HTTPException(status_code=404, detail=f"字幕包 {payload.pack} 不存在")
    return {"ok": True, "message": "待发段已按收件方与片段校验刷新", "summary": summary}


@router.get("/plan")
def get_plan(
    pack: str = Query(...), recipient: str = Query(...),
) -> dict[str, Any]:
    """发送页继续时复查：已发区间、抵扣区间、待发区间与实际停止位置。"""
    service = _service()
    summary = service.plan_summary(pack.strip(), recipient.strip())
    if summary is None:
        raise HTTPException(status_code=404, detail=f"字幕包 {pack} 不存在")
    return summary


@router.post("/claim")
def claim_segment(payload: SegmentPayload) -> dict[str, Any]:
    """领取片段：待生成 → 已领取。"""
    service = _service()
    ok, message, summary = service.claim_segment(
        payload.pack.strip(), payload.recipient.strip(), payload.segment)
    return {"ok": ok, "message": message, "summary": summary}


@router.post("/progress")
def record_progress(payload: ProgressPayload) -> dict[str, Any]:
    """登记送达位置：写入所属片段的已送至行号。"""
    service = _service()
    ok, message, summary = service.record_progress(
        payload.pack.strip(), payload.recipient.strip(), payload.upto_line)
    return {"ok": ok, "message": message, "summary": summary}


@router.post("/sign")
def sign_segment(payload: SegmentPayload) -> dict[str, Any]:
    """签收片段：重送签收不重复计行，迟到签收反向抵扣同内容待发段。"""
    service = _service()
    ok, message, summary = service.sign_segment(
        payload.pack.strip(), payload.recipient.strip(), payload.segment)
    return {"ok": ok, "message": message, "summary": summary}


@router.get("/vouchers/{voucher}")
def lookup_voucher(voucher: str) -> dict[str, Any]:
    """凭据复查：按签收凭据找回关联交付段，刷新后结果一致。"""
    service = _service()
    records = service.lookup_voucher(voucher)
    return {"ok": bool(records), "records": records}


@router.get("/remaining")
def remaining_file(
    pack: str = Query(...), recipient: str = Query(...),
) -> PlainTextResponse:
    """下载实际的剩余字幕文件：待发片段中尚未送出的字幕行。"""
    service = _service()
    result = service.remaining_lines(pack.strip(), recipient.strip())
    if result is None:
        raise HTTPException(status_code=404, detail=f"字幕包 {pack} 不存在")
    filename, text, _ = result
    return PlainTextResponse(
        text or "（无剩余字幕行）",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )
