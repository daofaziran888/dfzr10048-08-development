/** 纯前端数据层的公共类型：与全栈版后端返回的结构保持一致，换回后端时页面不用改。 */

export type EntryRow = {
  id: number
  status: string
  pending: boolean
  abnormal: boolean
  [field: string]: string | number | boolean
}

export type ModuleMeta = {
  key: string
  name: string
  entity: string
  desc: string
  fields: string[]
  statuses: string[]
  actions: string[]
  actionTargets: Record<string, string>
  metrics: string[]
}

export type PageResult = {
  items: EntryRow[]
  total: number
  page: number
  size: number
}

export type ActionResult = {
  ok: boolean
  message: string
}

export type OverviewResult = {
  cards: { label: string; value: number }[]
  modules: { name: string; created: number; pending: number; abnormal: number }[]
}

/** 分段交付计划里的单个片段。 */
export type SegmentPlanRow = {
  id: number
  seg_no: number
  range: string
  start: number
  end: number
  checksum: string
  status: string
  sent_upto: number
  credit_source: string
  voucher: string
  duplicate: string
}

/** 发送页计划汇总：已发/抵扣/待发区间与实际停止位置。 */
export type PlanSummary = {
  pack: string
  recipient: string
  line_count: number
  segments: SegmentPlanRow[]
  sent_ranges: [number, number][]
  credited_ranges: [number, number][]
  pending_ranges: [number, number][]
  stop_line: number
  remaining_count: number
  receipt: {
    回执编号: string
    status: string
    交付行数: string
    重交次数: string
    交付结论: string
  } | null
}

export type PlanActionResult = {
  ok: boolean
  message: string
  summary: PlanSummary | null
}

/** 凭据复查返回的关联交付段。 */
export type VoucherRecord = {
  交付段编号: string
  关联字幕包: string
  接收方标识: string
  片段区间: string
  status: string
  已送至行号: string
  抵扣来源: string
  签收凭据: string
  重复签收: string
}

export type VoucherResult = {
  ok: boolean
  records: VoucherRecord[]
}
