<script setup lang="ts">
import { computed } from 'vue'

type StatusType = 'completed' | 'failed' | 'running' | 'queued' | 'cancelled' | 'pending' | 'analyzed' | 'fixed' | 'wont_fix'
type SeverityType = 'critical' | 'high' | 'medium' | 'low'

const props = defineProps<{
  status?: StatusType
  severity?: SeverityType
  label?: string
}>()

const badgeClass = computed(() => {
  const base = 'status-badge'
  if (props.status) {
    return `${base} status-${props.status}`
  }
  if (props.severity) {
    return `${base} severity-${props.severity}`
  }
  return base
})

const displayLabel = computed(() => {
  if (props.label) return props.label
  
  const statusLabels: Record<StatusType, string> = {
    completed: '已完成',
    failed: '失败',
    running: '运行中',
    queued: '排队中',
    cancelled: '已取消',
    pending: '待处理',
    analyzed: '已分析',
    fixed: '已修复',
    wont_fix: '暂不修复'
  }
  
  const severityLabels: Record<SeverityType, string> = {
    critical: '严重',
    high: '高',
    medium: '中',
    low: '低'
  }
  
  return props.status ? statusLabels[props.status] : props.severity ? severityLabels[props.severity] : ''
})
</script>

<template>
  <span :class="badgeClass">
    {{ displayLabel }}
  </span>
</template>

<style scoped>
.status-badge {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  padding: 4px 10px;
  border-radius: 999px;
  font-size: 12px;
  font-weight: 600;
  white-space: nowrap;
}

.status-completed {
  background: rgba(16, 185, 129, 0.1);
  color: #059669;
}

.status-failed,
.severity-critical {
  background: rgba(239, 68, 68, 0.1);
  color: #dc2626;
}

.status-running {
  background: rgba(37, 99, 235, 0.1);
  color: #1d4ed8;
}

.status-queued {
  background: rgba(148, 163, 184, 0.1);
  color: #64748b;
}

.status-cancelled {
  background: rgba(148, 163, 184, 0.1);
  color: #64748b;
}

.status-pending {
  background: rgba(245, 158, 11, 0.1);
  color: #d97706;
}

.status-analyzed {
  background: rgba(124, 58, 237, 0.1);
  color: #7c3aed;
}

.status-fixed {
  background: rgba(16, 185, 129, 0.1);
  color: #059669;
}

.status-wont_fix {
  background: rgba(148, 163, 184, 0.1);
  color: #64748b;
}

.severity-high {
  background: rgba(239, 68, 68,