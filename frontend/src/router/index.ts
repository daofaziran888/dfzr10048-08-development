import { createRouter, createWebHistory } from 'vue-router'

import Overview from '@/views/Overview.vue'
import SegmentSendView from '@/views/SegmentSendView.vue'
import WorkbenchView from '@/views/WorkbenchView.vue'

const router = createRouter({
  history: createWebHistory(),
  routes: [
    { path: '/', name: 'overview', component: Overview },
    // 分段交付发送页：静态路径优先于通用模块路径匹配
    { path: '/segment_delivery', name: 'segment-send', component: SegmentSendView },
    { path: '/:moduleKey', name: 'module', component: WorkbenchView, props: true },
  ],
})

export default router
