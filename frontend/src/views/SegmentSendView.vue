<template>
  <section class="page" data-module="segment_delivery">
    <header class="page-head">
      <div>
        <h2>分段交付 · 发送页</h2>
        <p class="page-desc">
          待发范围由服务端按收件方及片段校验决定：旧签收仅抵扣同一收件方、内容相同的区间，
          改过的行必须送达，重送签收不重复计行。
        </p>
      </div>
      <div class="page-actions">
        <button
          class="btn"
          data-testid="download-remaining"
          type="button"
          :disabled="!summary || summary.remaining_count === 0"
          @click="downloadRemainingFile"
        >
          下载剩余字幕文件<template v-if="summary">（{{ summary.remaining_count }} 行）</template>
        </button>
      </div>
    </header>

    <form class="filter-bar" @submit.prevent="refreshPlan">
      <label class="filter-item">
        <span>字幕包</span>
        <select v-model="packCode" data-testid="select-pack">
          <option v-for="pack in packs" :key="pack" :value="pack">{{ pack }}</option>
        </select>
      </label>
      <label class="filter-item">
        <span>接收方</span>
        <select v-model="recipient" data-testid="select-recipient">
          <option v-for="item in recipients" :key="item" :value="item">{{ item }}</option>
        </select>
      </label>
      <button class="btn primary" data-testid="refresh-plan" type="submit" :disabled="!packCode || !recipient">
        生成/刷新待发段（局部补发）
      </button>
    </form>
    <p v-if="packCode && !recipients.length" class="empty-hint">
      该字幕包暂无交付回执，请先在「交付回执」登记接收方。
    </p>

    <template v-if="summary">
      <div class="stat-row">
        <article class="stat-card">
          <span class="stat-label">已发区间</span>
          <strong class="stat-value" data-testid="summary-sent">{{ rangesText(summary.sent_ranges) }}</strong>
        </article>
        <article class="stat-card">
          <span class="stat-label">抵扣区间</span>
          <strong class="stat-value" data-testid="summary-credited">{{ rangesText(summary.credited_ranges) }}</strong>
        </article>
        <article class="stat-card">
          <span class="stat-label">待发区间</span>
          <strong class="stat-value" data-testid="summary-pending">{{ rangesText(summary.pending_ranges) }}</strong>
        </article>
        <article class="stat-card">
          <span class="stat-label">实际停止位置</span>
          <strong class="stat-value" data-testid="summary-stop">
            {{ summary.stop_line > 0 ? `第 ${summary.stop_line} 行` : '未开始' }}
          </strong>
        </article>
      </div>

      <p v-if="summary.receipt" class="status-legend" data-testid="receipt-brief">
        回执 {{ summary.receipt.回执编号 }}：{{ summary.receipt.status }}，
        交付行数 {{ summary.receipt.交付行数 }}，重交次数 {{ summary.receipt.重交次数 }}，
        结论 {{ summary.receipt.交付结论 }}
      </p>

      <table class="data-table">
        <thead>
          <tr>
            <th>片段区间</th>
            <th>内容校验和</th>
            <th>当前状态</th>
            <th>已送至行号</th>
            <th>抵扣来源</th>
            <th>签收凭据</th>
            <th>重复签收</th>
            <th>可执行动作</th>
          </tr>
        </thead>
        <tbody>
          <tr
            v-for="segment in summary.segments"
            :key="segment.seg_no"
            :data-testid="`seg-row-${segment.seg_no}`"
            :data-status="segment.status"
          >
            <td>{{ segment.range }}</td>
            <td>{{ segment.checksum }}</td>
            <td>{{ segment.status }}</td>
            <td>{{ segment.sent_upto > 0 ? segment.sent_upto : '—' }}</td>
            <td>{{ segment.credit_source }}</td>
            <td>{{ segment.voucher }}</td>
            <td>{{ segment.duplicate }}</td>
            <td class="row-actions">
              <button
                v-if="segment.status === '待生成'"
                class="link"
                type="button"
                :data-testid="`claim-${segment.seg_no}`"
                @click="claim(segment)"
              >
                领取片段
              </button>
              <button
                v-if="segment.status === '已领取' || segment.status === '已抵扣'"
                class="link"
                type="button"
                :data-testid="`send-full-${segment.seg_no}`"
                @click="sendFull(segment)"
              >
                送达至段末
              </button>
              <button
                v-if="segment.status === '已领取' || segment.status === '已抵扣'"
                class="link"
                type="button"
                :data-testid="`sign-${segment.seg_no}`"
                @click="sign(segment)"
              >
                确认签收
              </button>
              <span v-if="segment.status === '已签收'" class="muted">已办结</span>
            </td>
          </tr>
          <tr v-if="!summary.segments.length">
            <td colspan="8" class="empty-state">该字幕包没有可交付的字幕行</td>
          </tr>
        </tbody>
      </table>

      <form class="filter-bar" @submit.prevent="recordManualProgress">
        <label class="filter-item">
          <span>送达至行号</span>
          <input
            v-model.number="uptoLine"
            data-testid="input-upto-line"
            type="number"
            min="1"
            :max="summary.line_count"
            :placeholder="`1-${summary.line_count}`"
          />
        </label>
        <button class="btn" data-testid="record-progress" type="submit">登记送达</button>
      </form>
    </template>

    <form class="filter-bar" @submit.prevent="runVoucherCheck">
      <label class="filter-item">
        <span>签收凭据复查</span>
        <input
          v-model="voucher"
          data-testid="input-voucher"
          placeholder="输入签收凭据，如 SGV-SBPK-0005-S1-0002"
        />
      </label>
      <button class="btn" data-testid="check-voucher" type="submit" :disabled="!voucher.trim()">
        复查凭据
      </button>
    </form>

    <div v-if="voucherChecked" class="voucher-result" data-testid="voucher-result">
      <table v-if="voucherRecords.length" class="data-table">
        <thead>
          <tr>
            <th>交付段编号</th>
            <th>关联字幕包</th>
            <th>接收方标识</th>
            <th>片段区间</th>
            <th>当前状态</th>
            <th>已送至行号</th>
            <th>抵扣来源</th>
            <th>重复签收</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="record in voucherRecords" :key="record.交付段编号">
            <td>{{ record.交付段编号 }}</td>
            <td>{{ record.关联字幕包 }}</td>
            <td>{{ record.接收方标识 }}</td>
            <td>{{ record.片段区间 }}</td>
            <td>{{ record.status }}</td>
            <td>{{ record.已送至行号 }}</td>
            <td>{{ record.抵扣来源 }}</td>
            <td>{{ record.重复签收 }}</td>
          </tr>
        </tbody>
      </table>
      <p v-else class="empty-hint">凭据无效：没有查到对应的签收记录</p>
    </div>

    <footer class="page-foot">
      <span v-if="message" class="ok-text">{{ message }}</span>
      <span v-if="errorMessage" class="error-text">{{ errorMessage }}</span>
    </footer>
  </section>
</template>

<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import { useRoute } from 'vue-router'

import {
  checkVoucher,
  claimSegment,
  downloadRemaining,
  fetchPlan,
  generatePlan,
  listEntries,
  recordProgress,
  signSegment,
} from '@/api/backend-service'
import type { PlanSummary, SegmentPlanRow, VoucherRecord } from '@/data/types'

const route = useRoute()
const packs = ref<string[]>([])
const receiptRows = ref<Record<string, string>[]>([])
const packCode = ref('')
const recipient = ref('')
const summary = ref<PlanSummary | null>(null)
const uptoLine = ref<number | null>(null)
const voucher = ref('')
const voucherRecords = ref<VoucherRecord[]>([])
const voucherChecked = ref(false)
const message = ref('')
const errorMessage = ref('')

const recipients = computed(() => {
  const names = receiptRows.value
    .filter((row) => row['关联字幕包'] === packCode.value)
    .map((row) => row['接收方标识'])
  return [...new Set(names)]
})

function rangesText(ranges: [number, number][]): string {
  return ranges.length ? ranges.map(([low, high]) => `${low}-${high}`).join('、') : '—'
}

function report(result: { ok: boolean; message: string }) {
  if (result.ok) {
    message.value = result.message
    errorMessage.value = ''
  } else {
    errorMessage.value = result.message
    message.value = ''
  }
}

async function guard(action: () => Promise<void>) {
  errorMessage.value = ''
  message.value = ''
  try {
    await action()
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '操作失败'
  }
}

async function refreshPlan() {
  await guard(async () => {
    const result = await generatePlan(packCode.value, recipient.value)
    summary.value = result.summary
    report(result)
  })
}

async function claim(segment: SegmentPlanRow) {
  await guard(async () => {
    const result = await claimSegment(packCode.value, recipient.value, segment.seg_no)
    if (result.summary) summary.value = result.summary
    report(result)
  })
}

async function sendFull(segment: SegmentPlanRow) {
  await guard(async () => {
    const result = await recordProgress(packCode.value, recipient.value, segment.end)
    if (result.summary) summary.value = result.summary
    report(result)
  })
}

async function sign(segment: SegmentPlanRow) {
  await guard(async () => {
    const result = await signSegment(packCode.value, recipient.value, segment.seg_no)
    if (result.summary) summary.value = result.summary
    report(result)
  })
}

async function recordManualProgress() {
  if (!uptoLine.value) {
    errorMessage.value = '请输入送达至行号'
    return
  }
  await guard(async () => {
    const result = await recordProgress(packCode.value, recipient.value, Number(uptoLine.value))
    if (result.summary) summary.value = result.summary
    report(result)
  })
}

async function runVoucherCheck() {
  await guard(async () => {
    const result = await checkVoucher(voucher.value.trim())
    voucherRecords.value = result.records
    voucherChecked.value = true
  })
}

async function downloadRemainingFile() {
  await guard(async () => {
    await downloadRemaining(packCode.value, recipient.value)
    message.value = '剩余字幕文件已下载'
  })
}

watch(packCode, () => {
  recipient.value = recipients.value[0] ?? ''
  summary.value = null
})

watch(recipient, () => {
  summary.value = null
})

onMounted(async () => {
  await guard(async () => {
    const [packPage, receiptPage] = await Promise.all([
      listEntries('subtitle_pack'),
      listEntries('delivery_receipt'),
    ])
    packs.value = packPage.items.map((row) => String(row['字幕包编号']))
    receiptRows.value = receiptPage.items.map((row) => ({
      关联字幕包: String(row['关联字幕包']),
      接收方标识: String(row['接收方标识']),
    }))
    const queryPack = String(route.query.pack ?? '')
    const queryRecipient = String(route.query.recipient ?? '')
    packCode.value = packs.value.includes(queryPack) ? queryPack : (packs.value[0] ?? '')
    if (queryRecipient && recipients.value.includes(queryRecipient)) {
      recipient.value = queryRecipient
    }
    // 从交付回执「局部补发」跳转进来时直接展示当前计划
    if (packCode.value && recipient.value) {
      const existing = await fetchPlan(packCode.value, recipient.value)
      if (existing && existing.segments.length) {
        summary.value = existing
      }
    }
  })
})
</script>

<style scoped>
select,
input {
  padding: 6px 10px;
  border: 1px solid var(--border);
  border-radius: 6px;
  font-size: 13px;
}
.ok-text {
  color: #15803d;
}
.muted {
  color: var(--muted);
  font-size: 12px;
}
.empty-hint {
  color: var(--muted);
  font-size: 13px;
}
.voucher-result {
  margin-top: 8px;
}
</style>
