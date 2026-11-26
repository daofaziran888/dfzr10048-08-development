import { request } from './client'
import { moduleMeta } from '@/data/modules'
import type {
  ActionResult,
  EntryRow,
  OverviewResult,
  PageResult,
  PlanActionResult,
  PlanSummary,
  VoucherResult,
} from '@/data/types'

export { moduleMeta }

export async function listEntries(
  key: string,
  filters: Record<string, string> = {},
): Promise<PageResult> {
  const query = new URLSearchParams(filters).toString()
  const response = await request(`/api/${key}?${query}`)
  if (!response.ok) throw new Error('业务数据读取失败')
  return await response.json()
}

export async function createEntry(
  key: string,
  values: Record<string, string>,
): Promise<ActionResult> {
  const response = await request(`/api/${key}`, {
    method: 'POST',
    body: JSON.stringify({ values }),
  })
  return await response.json()
}

export async function runAction(
  key: string,
  id: number,
  action: string,
): Promise<ActionResult> {
  const response = await request(`/api/${key}/${id}/actions`, {
    method: 'POST',
    body: JSON.stringify({ action }),
  })
  return await response.json()
}

export async function downloadEntries(
  key: string,
  filters: Record<string, string> = {},
): Promise<void> {
  const query = new URLSearchParams(filters).toString()
  const response = await request(`/api/${key}/export?${query}`)
  if (!response.ok) throw new Error('业务数据导出失败')
  const url = URL.createObjectURL(await response.blob())
  const anchor = document.createElement('a')
  anchor.href = url
  anchor.download = `${key}.csv`
  document.body.appendChild(anchor)
  anchor.click()
  document.body.removeChild(anchor)
  URL.revokeObjectURL(url)
}

export async function loadOverview(): Promise<OverviewResult> {
  const response = await request('/api/overview')
  if (!response.ok) throw new Error('运营概览读取失败')
  return await response.json()
}

/* ---------- 分段交付：局部补发与发送页 ---------- */

async function planAction(path: string, body: Record<string, unknown>): Promise<PlanActionResult> {
  const response = await request(path, {
    method: 'POST',
    body: JSON.stringify(body),
  })
  const payload = (await response.json()) as PlanActionResult & { detail?: string }
  if (!response.ok) throw new Error(payload.detail ?? '分段交付操作失败')
  return payload
}

/** 生成/刷新局部补发计划：待发范围由服务端按收件方及片段校验决定。 */
export function generatePlan(pack: string, recipient: string): Promise<PlanActionResult> {
  return planAction('/api/segment_delivery/plan', { pack, recipient })
}

/** 读取当前计划：回到发送页时确认已发与抵扣区间。 */
export async function fetchPlan(pack: string, recipient: string): Promise<PlanSummary | null> {
  const query = new URLSearchParams({ pack, recipient }).toString()
  const response = await request(`/api/segment_delivery/plan?${query}`)
  if (response.status === 404) return null
  if (!response.ok) throw new Error('分段交付计划读取失败')
  return (await response.json()) as PlanSummary
}

/** 领取片段：待生成 → 已领取。 */
export function claimSegment(pack: string, recipient: string, segment: number): Promise<PlanActionResult> {
  return planAction('/api/segment_delivery/claim', { pack, recipient, segment })
}

/** 登记送达位置（已送至行号）。 */
export function recordProgress(pack: string, recipient: string, uptoLine: number): Promise<PlanActionResult> {
  return planAction('/api/segment_delivery/progress', { pack, recipient, upto_line: uptoLine })
}

/** 签收片段：重送签收不重复计行。 */
export function signSegment(pack: string, recipient: string, segment: number): Promise<PlanActionResult> {
  return planAction('/api/segment_delivery/sign', { pack, recipient, segment })
}

/** 凭据复查：刷新后按原凭据核对签收记录。 */
export async function checkVoucher(voucher: string): Promise<VoucherResult> {
  const response = await request(`/api/segment_delivery/vouchers/${encodeURIComponent(voucher)}`)
  if (!response.ok) throw new Error('凭据复查失败')
  return (await response.json()) as VoucherResult
}

/** 下载实际的剩余字幕文件。 */
export async function downloadRemaining(pack: string, recipient: string): Promise<void> {
  const query = new URLSearchParams({ pack, recipient }).toString()
  const response = await request(`/api/segment_delivery/remaining?${query}`)
  if (!response.ok) throw new Error('剩余字幕文件读取失败')
  const url = URL.createObjectURL(await response.blob())
  const anchor = document.createElement('a')
  anchor.href = url
  anchor.download = `${pack}-remaining.txt`
  document.body.appendChild(anchor)
  anchor.click()
  document.body.removeChild(anchor)
  URL.revokeObjectURL(url)
}
