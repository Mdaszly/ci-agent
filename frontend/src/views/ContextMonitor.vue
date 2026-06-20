<script setup lang="ts">
import { ref } from 'vue';
import { getTaskContext, type ContextUsage, type ContextNodeUsage } from '../services/api';

const taskId = ref('');
const contextData = ref<ContextUsage | null>(null);
const loading = ref(false);
const errorMsg = ref('');

async function handleQuery() {
  if (!taskId.value.trim()) return;
  loading.value = true;
  errorMsg.value = '';
  contextData.value = null;
  try {
    contextData.value = await getTaskContext(taskId.value.trim());
  } catch (err) {
    errorMsg.value = err instanceof Error ? err.message : '查询失败';
  } finally {
    loading.value = false;
  }
}

function utilizationColor(u: number): string {
  if (u >= 0.8) return '#dc2626';
  if (u >= 0.6) return '#d97706';
  return '#16a34a';
}

function utilizationPercent(u: number): string {
  return `${(u * 100).toFixed(1)}%`;
}

function formatTokens(n: number): string {
  if (n >= 1000) return `${(n / 1000).toFixed(1)}k`;
  return String(n);
}
</script>

<template>
  <div class="context-monitor">
    <div class="page-header">
      <h1 class="page-title">上下文监控</h1>
      <p class="page-desc">查看各工作流节点的 token 用量与 context window 使用率</p>
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

    <div v-if="contextData" class="context-content">
      <!-- 总览 -->
      <div class="overview-row">
        <div class="overview-cell">
          <span class="overview-value">{{ formatTokens(contextData.total_tokens) }}</span>
          <span class="overview-label">总 token 消耗</span>
        </div>
        <div class="overview-cell">
          <span class="overview-value">{{ formatTokens(contextData.context_limit) }}</span>
          <span class="overview-label">Context 上限</span>
        </div>
        <div class="overview-cell">
          <span
            class="overview-value"
            :style="{ color: utilizationColor(contextData.total_tokens / contextData.context_limit) }"
          >
            {{ utilizationPercent(contextData.total_tokens / contextData.context_limit) }}
          </span>
          <span class="overview-label">总使用率</span>
        </div>
      </div>

      <!-- 各节点明细 -->
      <h2 class="section-title">节点明细</h2>
      <div class="node-list">
        <div v-for="node in contextData.nodes" :key="node.node" class="node-row">
          <div class="node-info">
            <span class="node-name">{{ node.node }}</span>
            <span class="node-tokens">
              {{ formatTokens(node.prompt_tokens) }} prompt + {{ formatTokens(node.completion_tokens) }} completion
            </span>
          </div>
          <div class="node-bar-wrap">
            <div class="node-bar-track">
              <div
                class="node-bar-fill"
                :style="{
                  width: utilizationPercent(node.utilization),
                  background: utilizationColor(node.utilization),
                }"
              />
            </div>
            <span class="node-bar-label" :style="{ color: utilizationColor(node.utilization) }">
              {{ utilizationPercent(node.utilization) }}
            </span>
          </div>
        </div>
      </div>
    </div>

      <p v-if="!contextData && !loading && !errorMsg" class="empty-state">
        输入任务标识查看上下文使用情况
      </p>
  </div>
</template>

<style scoped>
.context-monitor {
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

.overview-row {
  display: flex;
  gap: 16px;
  margin-bottom: 28px;
}

.overview-cell {
  flex: 1;
  background: rgba(255, 255, 255, 0.8);
  border: 1px solid rgba(148, 163, 184, 0.2);
  border-radius: 10px;
  padding: 16px 20px;
  display: flex;
  flex-direction: column;
  gap: 4px;
}

.overview-value {
  font-size: 28px;
  font-weight: 700;
  color: #0f172a;
}

.overview-label {
  font-size: 12px;
  color: #64748b;
}

.section-title {
  font-size: 16px;
  font-weight: 600;
  color: #0f172a;
  margin: 0 0 16px;
}

.node-list {
  display: flex;
  flex-direction: column;
  gap: 12px;
}

.node-row {
  background: rgba(255, 255, 255, 0.9);
  border: 1px solid rgba(148, 163, 184, 0.2);
  border-radius: 10px;
  padding: 14px 18px;
}

.node-info {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 8px;
}

.node-name {
  font-size: 14px;
  font-weight: 600;
  color: #0f172a;
}

.node-tokens {
  font-size: 12px;
  color: #94a3b8;
}

.node-bar-wrap {
  display: flex;
  align-items: center;
  gap: 10px;
}

.node-bar-track {
  flex: 1;
  height: 8px;
  background: #f1f5f9;
  border-radius: 4px;
  overflow: hidden;
}

.node-bar-fill {
  height: 100%;
  border-radius: 4px;
  transition: width 0.3s ease;
}

.node-bar-label {
  font-size: 13px;
  font-weight: 600;
  min-width: 50px;
  text-align: right;
}

.empty-state {
  text-align: center;
  padding: 48px 0;
  color: #94a3b8;
  font-size: 14px;
}
</style>
