<script setup lang="ts">
import { ref } from 'vue';
import { getTaskCheckpoints, type WorkflowCheckpoint } from '../services/api';

const taskId = ref('');
const checkpoints = ref<WorkflowCheckpoint[]>([]);
const loading = ref(false);
const errorMsg = ref('');
const expandedId = ref<string | null>(null);

async function handleQuery() {
  if (!taskId.value.trim()) return;
  loading.value = true;
  errorMsg.value = '';
  checkpoints.value = [];
  try {
    checkpoints.value = await getTaskCheckpoints(taskId.value.trim());
  } catch (err) {
    errorMsg.value = err instanceof Error ? err.message : '查询失败';
  } finally {
    loading.value = false;
  }
}

function toggleExpand(id: string) {
  expandedId.value = expandedId.value === id ? null : id;
}

function formatTime(iso: string): string {
  return new Date(iso).toLocaleString('zh-CN', { hour12: false });
}

const kindLabels: Record<string, string> = {
  stage_start: '阶段开始',
  stage_end: '阶段结束',
  workflow_start: '工作流启动',
  workflow_end: '工作流完成',
  iteration: '迭代',
  error: '错误',
};

const statusColors: Record<string, string> = {
  ok: '#16a34a',
  running: '#2563eb',
  failed: '#dc2626',
  completed: '#16a34a',
};
</script>

<template>
  <div class="checkpoint-timeline">
    <div class="page-header">
      <h1 class="page-title">检查点时间线</h1>
      <p class="page-desc">查看工作流状态快照，支持回溯与 Time Travel 调试</p>
    </div>

    <div class="query-bar">
      <input
        v-model="taskId"
        type="text"
        class="task-input"
        placeholder="输入任务标识…"
        @keyup.enter="handleQuery"
      />
      <button class="query-btn" :disabled="loading" @click="handleQuery">
        {{ loading ? '查询中…' : '查询' }}
      </button>
    </div>

    <p v-if="errorMsg" class="error-text">{{ errorMsg }}</p>

    <div v-if="checkpoints.length" class="timeline">
      <div
        v-for="(cp, idx) in checkpoints"
        :key="cp.checkpoint_id"
        class="timeline-item"
        :class="{ expanded: expandedId === cp.checkpoint_id }"
      >
        <div class="timeline-marker">
          <div
            class="marker-dot"
            :style="{ background: statusColors[cp.status] || '#94a3b8' }"
          />
          <div v-if="idx < checkpoints.length - 1" class="marker-line" />
        </div>

        <div class="timeline-content" @click="toggleExpand(cp.checkpoint_id)">
          <div class="timeline-header">
            <span class="checkpoint-kind">
              {{ kindLabels[cp.kind] || cp.kind }}
            </span>
            <span v-if="cp.stage" class="checkpoint-stage">{{ cp.stage }}</span>
            <span
              class="checkpoint-status"
              :style="{ color: statusColors[cp.status] || '#94a3b8' }"
            >
              {{ cp.status }}
            </span>
            <span class="checkpoint-time">{{ formatTime(cp.created_at) }}</span>
          </div>

          <div v-if="expandedId === cp.checkpoint_id" class="checkpoint-detail">
            <div class="detail-row">
              <span class="detail-label">检查点 ID</span>
              <code>{{ cp.checkpoint_id }}</code>
            </div>
            <div class="detail-row">
              <span class="detail-label">运行 ID</span>
              <code>{{ cp.run_id }}</code>
            </div>
            <div v-if="cp.thread_id" class="detail-row">
              <span class="detail-label">线程 ID</span>
              <code>{{ cp.thread_id }}</code>
            </div>
            <div v-if="Object.keys(cp.payload).length" class="detail-row">
              <span class="detail-label">负载</span>
              <pre class="payload-json">{{ JSON.stringify(cp.payload, null, 2) }}</pre>
            </div>
          </div>
        </div>
      </div>
    </div>

      <p v-if="!loading && !checkpoints.length && !errorMsg" class="empty-state">
        输入任务标识查看检查点时间线
      </p>
  </div>
</template>

<style scoped>
.checkpoint-timeline {
  max-width: 960px;
  margin: 0 auto;
}

.page-header {
  margin-bottom: 28px;
}

.page-title {
  font-size: 24px;
  font-weight: 700;
  color: #0f172a;
  margin: 0 0 4px;
}

.page-desc {
  font-size: 14px;
  color: #64748b;
  margin: 0;
}

.query-bar {
  display: flex;
  gap: 8px;
  margin-bottom: 24px;
}

.task-input {
  flex: 1;
  padding: 10px 14px;
  border: 1px solid rgba(148, 163, 184, 0.4);
  border-radius: 8px;
  font-size: 14px;
  background: #fff;
}

.task-input:focus {
  outline: none;
  border-color: #2563eb;
  box-shadow: 0 0 0 3px rgba(37, 99, 235, 0.12);
}

.query-btn {
  padding: 10px 20px;
  border: none;
  border-radius: 8px;
  background: #2563eb;
  color: #fff;
  font-size: 14px;
  font-weight: 600;
  cursor: pointer;
}

.query-btn:disabled {
  opacity: 0.6;
}

.error-text {
  color: #dc2626;
  font-size: 13px;
}

.timeline {
  display: flex;
  flex-direction: column;
}

.timeline-item {
  display: flex;
  gap: 16px;
  min-height: 48px;
}

.timeline-marker {
  display: flex;
  flex-direction: column;
  align-items: center;
  flex-shrink: 0;
  width: 16px;
}

.marker-dot {
  width: 12px;
  height: 12px;
  border-radius: 50%;
  border: 2px solid #fff;
  box-shadow: 0 0 0 1px rgba(148, 163, 184, 0.3);
  flex-shrink: 0;
}

.marker-line {
  width: 2px;
  flex: 1;
  background: rgba(148, 163, 184, 0.25);
  margin: 2px 0;
}

.timeline-content {
  flex: 1;
  background: rgba(255, 255, 255, 0.9);
  border: 1px solid rgba(148, 163, 184, 0.2);
  border-radius: 10px;
  padding: 12px 16px;
  margin-bottom: 8px;
  cursor: pointer;
  transition: border-color 0.18s ease;
}

.timeline-content:hover {
  border-color: rgba(37, 99, 235, 0.3);
}

.timeline-item.expanded .timeline-content {
  border-color: rgba(37, 99, 235, 0.4);
}

.timeline-header {
  display: flex;
  align-items: center;
  gap: 10px;
  flex-wrap: wrap;
}

.checkpoint-kind {
  font-size: 14px;
  font-weight: 600;
  color: #0f172a;
}

.checkpoint-stage {
  font-size: 12px;
  padding: 2px 8px;
  background: #eff6ff;
  border-radius: 4px;
  color: #2563eb;
}

.checkpoint-status {
  font-size: 12px;
  font-weight: 600;
}

.checkpoint-time {
  font-size: 12px;
  color: #94a3b8;
  margin-left: auto;
}

.checkpoint-detail {
  margin-top: 12px;
  padding-top: 12px;
  border-top: 1px solid rgba(148, 163, 184, 0.15);
}

.detail-row {
  margin-bottom: 8px;
  display: flex;
  flex-direction: column;
  gap: 4px;
}

.detail-label {
  font-size: 12px;
  font-weight: 600;
  color: #475569;
}

.detail-row code {
  font-size: 12px;
  padding: 4px 8px;
  background: #f1f5f9;
  border-radius: 4px;
  color: #475569;
  align-self: flex-start;
}

.payload-json {
  font-size: 12px;
  background: #f8fafc;
  border-radius: 6px;
  padding: 10px;
  overflow-x: auto;
  color: #334155;
  margin: 0;
}

.empty-state {
  text-align: center;
  padding: 48px 0;
  color: #94a3b8;
  font-size: 14px;
}
</style>
