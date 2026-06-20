<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref } from 'vue';
import { useRoute, useRouter } from 'vue-router';
import { ElBadge, ElButton, ElCard, ElDivider, ElMessage, ElTable, ElTableColumn, ElTag } from 'element-plus';
import type { EChartsOption } from 'echarts';

import EChartPanel from '../components/EChartPanel.vue';
import { getDecisionPack, getTask, getTaskMemory, getTaskContext, subscribeTaskEvents, type ContextUsage, type DecisionAction, type DecisionPack, type Evidence, type RichTaskRecord, type TaskEvent, type TaskMemoryResponse } from '../services/api';

const route = useRoute();
const router = useRouter();
const taskId = computed(() => route.params.id as string);
const task = ref<RichTaskRecord | null>(null);
const loading = ref(true);
const fallbackDecisionPack = ref<DecisionPack | null>(null);
const taskMemory = ref<TaskMemoryResponse | null>(null);
const taskContext = ref<ContextUsage | null>(null);

const statusLabels: Record<string, string> = {
  completed: '已完成',
  failed: '失败',
  running: '运行中',
  queued: '排队中',
  cancelled: '已取消',
};

const riskLabels: Record<string, string> = {
  low: '低',
  medium: '中',
  high: '高',
};

const statusColors: Record<string, 'success' | 'danger' | 'warning' | 'info'> = {
  completed: 'success',
  failed: 'danger',
  running: 'warning',
  queued: 'info',
  cancelled: 'info',
};

const riskColors: Record<string, 'success' | 'warning' | 'danger'> = {
  low: 'success',
  medium: 'warning',
  high: 'danger',
};

const displayedDecisionPack = computed(() => task.value?.decision_pack ?? fallbackDecisionPack.value);
const rawDecisionPackText = computed(() => (displayedDecisionPack.value ? JSON.stringify(displayedDecisionPack.value, null, 2) : ''));
const positioningActions = computed<DecisionAction[]>(() => displayedDecisionPack.value?.positioning || []);
const mvpPriorities = computed<DecisionAction[]>(() => displayedDecisionPack.value?.mvp_priorities || []);
const pricingInsights = computed<DecisionAction[]>(() => displayedDecisionPack.value?.pricing_insights || []);
const battlecard = computed<DecisionAction[]>(() => displayedDecisionPack.value?.battlecard || []);
const evidenceList = computed<Evidence[]>(() => task.value?.evidence || []);
const events = computed<TaskEvent[]>(() => task.value?.events || []);
const decisionHistory = computed(() => task.value?.decision_history || []);
const memoryItems = computed(() => task.value?.memory_items || []);
const reviewHistory = computed(() => task.value?.review_history || []);
const decisionPackVisible = computed(() => Boolean(displayedDecisionPack.value));

const taskDisplayId = computed(() => task.value?.observable_id || task.value?.id || '-');

const decisionPackHiddenReason = computed(() => {
  const pack = displayedDecisionPack.value;
  const review = task.value?.review;

  if (!pack) return '当前任务尚未生成决策包';
  if (decisionPackVisible.value) return '';
  if (task.value?.status !== 'completed') return '任务尚未收口，决策包暂不对外展示';
  if (review?.hallucination_risk !== 'low') return `Reviewer 风险为 ${review?.hallucination_risk ?? 'unknown'}，已停止对外展示`;
  if ((review?.score ?? 0) < 0.8) return 'Reviewer 评分未达发布阈值，已停止对外展示';
  return `决策包状态为 ${pack.status}，已停止对外展示`;
});

let refreshTimer: ReturnType<typeof setInterval> | null = null;
let refreshInFlight = false;
let lastKnownEventCount = 0;
const currentRiskLabel = computed(() => riskLabels[task.value?.review?.hallucination_risk || 'medium'] || '-');
const currentRiskColor = computed(() => riskColors[task.value?.review?.hallucination_risk || 'medium'] || 'info');
const memoryState = computed(() => task.value?.memory_state ?? null);
const currentStatusLabel = computed(() => statusLabels[task.value?.status || ''] || task.value?.status || '');

const terminalReason = computed(() => {
  const status = task.value?.status;
  const memory = memoryState.value;
  const review = task.value?.review;
  const retries = memory?.iteration ?? 0;
  const maxRetries = memory?.max_iterations ?? 0;

  if (!task.value) return '等待任务加载';
  if (status === 'queued') return '任务已入队，等待执行';
  if (status === 'running') {
    if (memory?.retry_reason) return `当前卡点：${memory.retry_reason}`;
    if (review?.hallucination_risk === 'high') return 'Reviewer 检测到高幻觉风险，正在回流修复';
    return '任务仍在运行中';
  }
  if (status === 'completed') {
    return review?.score != null && review.score >= 0.8 ? 'Reviewer 达标并已发布' : '任务已完成，结果已收敛';
  }
  if (status === 'failed') {
    if (memory?.retry_reason) return memory.retry_reason;
    if (review?.hallucination_risk === 'high') return '规则校验或引用核验未通过';
    if (maxRetries > 0 && retries >= maxRetries) return '已达到最大重试轮次，任务停止';
    return '任务失败';
  }
  if (status === 'cancelled') return '任务已取消';
  return '状态未知';
});

const reflowTimelineChart = computed<EChartsOption | null>(() => {
  const items = decisionHistory.value;
  if (!items.length) return null;

  return {
    grid: { left: 36, right: 20, top: 32, bottom: 28, containLabel: true },
    tooltip: { trigger: 'axis' },
    xAxis: {
      type: 'category',
      data: items.map((item) => `v${item.version ?? '-'}`),
    },
    yAxis: { type: 'value' },
    series: [
      {
        type: 'bar',
        name: '版本号',
        data: items.map((item) => item.version ?? 0),
        itemStyle: { color: '#2563eb' },
      },
    ],
  };
});

const reviewTrendChart = computed<EChartsOption | null>(() => {
  const items = reviewHistory.value;
  if (!items.length) return null;

  return {
    grid: { left: 36, right: 36, top: 32, bottom: 28, containLabel: true },
    tooltip: { trigger: 'axis' },
    legend: { top: 0 },
    xAxis: {
      type: 'category',
      data: items.map((item, index) => item.created_at ? formatShortDate(item.created_at) : `#${index + 1}`),
    },
    yAxis: [
      { type: 'value', name: 'score', max: 100 },
      { type: 'value', name: 'risk', min: 1, max: 3, interval: 1 },
    ],
    series: [
      {
        type: 'line',
        name: 'score',
        data: items.map((item) => Math.round((item.score ?? 0) * 100)),
        smooth: true,
        itemStyle: { color: '#16a34a' },
      },
      {
        type: 'line',
        name: 'risk',
        yAxisIndex: 1,
        data: items.map((item) => riskValue(item.hallucination_risk)),
        smooth: true,
        itemStyle: { color: '#f59e0b' },
      },
    ],
  };
});

const recallChart = computed<EChartsOption | null>(() => {
  const items = memoryItems.value;
  if (!items.length) return null;

  return {
    grid: { left: 36, right: 20, top: 32, bottom: 48, containLabel: true },
    tooltip: { trigger: 'axis' },
    xAxis: {
      type: 'category',
      data: items.map((item) => item.id || '-'),
      axisLabel: { rotate: 25 },
    },
    yAxis: { type: 'value', max: 1 },
    series: [
      {
        type: 'bar',
        name: 'similarity',
        data: items.map((item) => item.similarity ?? 0),
        itemStyle: { color: '#0ea5e9' },
      },
    ],
  };
});

function riskValue(risk?: string) {
  if (risk === 'high') return 3;
  if (risk === 'medium') return 2;
  return 1;
}

function formatDate(dateStr?: string) {
  if (!dateStr) return '-';
  const date = new Date(dateStr);
  return Number.isNaN(date.getTime()) ? dateStr : date.toLocaleString('zh-CN');
}

function formatShortDate(dateStr?: string) {
  if (!dateStr) return '-';
  const date = new Date(dateStr);
  return Number.isNaN(date.getTime()) ? dateStr : date.toLocaleDateString('zh-CN', { month: '2-digit', day: '2-digit' });
}

function goBack() {
  router.push('/');
}

onMounted(async () => {
  try {
    const record = await getTask(taskId.value);
    task.value = record;

    if (!record.decision_pack) {
      try {
        const decisionPackResponse = await getDecisionPack(taskId.value);
        fallbackDecisionPack.value = decisionPackResponse.decision_pack;
        task.value = {
          ...record,
          decision_pack: decisionPackResponse.decision_pack,
          review: record.review ?? decisionPackResponse.review ?? null,
        };
      } catch (fallbackError) {
        if (fallbackError instanceof Error) {
          console.debug('[TaskDetail] 决策包兜底加载失败:', fallbackError.message);
        }
      }
    }

    // 并行加载记忆与上下文数据（失败不阻塞主流程）
    Promise.allSettled([
      getTaskMemory(taskId.value),
      getTaskContext(taskId.value),
    ]).then(([memResult, ctxResult]) => {
      if (memResult.status === 'fulfilled') taskMemory.value = memResult.value;
      if (ctxResult.status === 'fulfilled') taskContext.value = ctxResult.value;
    });
  } catch (error) {
    ElMessage.error(error instanceof Error ? error.message : '获取任务详情失败');
  } finally {
    loading.value = false;
  }
});
</script>

<template>
  <div class="task-detail-page">
    <div class="page-header">
      <button class="back-btn" @click="goBack">← 返回</button>
      <div>
        <h1>任务详情</h1>
        <p class="page-subtitle">回流轨迹、Reviewer 风险和决策包版本一屏查看</p>
      </div>
    </div>

    <div v-if="loading" class="loading">
      <div class="spinner"></div>
      <p>加载中...</p>
    </div>

    <template v-else-if="task">
      <ElCard class="task-info-card">
        <div class="task-header">
          <div class="task-title">
            <h2>{{ task.request.product_goal }}</h2>
            <ElBadge :type="statusColors[task.status]" :text="currentStatusLabel" />
          </div>
          <div class="task-meta">
            <span>任务标识: {{ taskDisplayId }}</span>
            <span>创建时间: {{ formatDate(task.created_at) }}</span>
          </div>
        </div>

        <ElDivider />

        <div class="task-metrics">
          <div class="metric-item">
            <span class="metric-label">竞品数量</span>
            <span class="metric-value">{{ task.request.competitors?.length || 0 }}</span>
          </div>
          <div class="metric-item">
            <span class="metric-label">证据数量</span>
            <span class="metric-value">{{ evidenceList.length }}</span>
          </div>
          <div class="metric-item">
            <span class="metric-label">Reviewer 评分</span>
            <span class="metric-value" :class="{ 'high-risk': task.review?.hallucination_risk === 'high' }">
              {{ task.review?.score != null ? `${(task.review.score * 100).toFixed(0)}分` : '-' }}
            </span>
          </div>
          <div class="metric-item">
            <span class="metric-label">幻觉风险</span>
            <ElTag :type="currentRiskColor">{{ currentRiskLabel }}</ElTag>
          </div>
        </div>

        <div class="task-footer">
          <span>{{ terminalReason }}</span>
          <span>版本：{{ memoryState?.current_pack_version ?? '-' }} · 回流轮次：{{ memoryState?.iteration ?? 0 }}</span>
          <span>最大轮次：{{ memoryState?.max_iterations ?? '-' }}</span>
        </div>
      </ElCard>

      <!-- 记忆状态面板 -->
      <ElCard v-if="taskMemory" class="memory-panel">
        <template #header>记忆状态</template>
        <div class="memory-grid">
          <div class="memory-cell">
            <span class="memory-value">{{ taskMemory.memory_state?.last_recall_count ?? 0 }}</span>
            <span class="memory-label">召回数量</span>
          </div>
          <div class="memory-cell">
            <span class="memory-value">{{ taskMemory.memory_items.length }}</span>
            <span class="memory-label">记忆块数</span>
          </div>
          <div class="memory-cell">
            <span class="memory-value">{{ taskMemory.memory_state?.current_iteration ?? 0 }}</span>
            <span class="memory-label">回流轮次</span>
          </div>
          <div class="memory-cell">
            <ElTag :type="taskMemory.memory_state?.last_reviewer_status === 'approved' ? 'success' : 'warning'" size="small">
              {{ taskMemory.memory_state?.last_reviewer_status ?? 'pending' }}
            </ElTag>
            <span class="memory-label">评审状态</span>
          </div>
        </div>
        <div v-if="taskMemory.memory_state?.last_recall_summary" class="memory-summary">
          <span class="memory-label">召回摘要</span>
          <p>{{ taskMemory.memory_state.last_recall_summary }}</p>
        </div>
        <div v-if="taskMemory.memory_state?.retry_reason" class="memory-retry">
          <ElTag type="danger" size="small">{{ taskMemory.memory_state.retry_reason }}</ElTag>
        </div>
      </ElCard>

      <!-- 上下文监控面板 -->
      <ElCard v-if="taskContext" class="context-panel">
        <template #header>上下文监控</template>
        <div class="context-overview">
          <span class="context-total">{{ taskContext.total_tokens.toLocaleString() }}</span>
          <span class="context-sep">/</span>
          <span class="context-limit">{{ taskContext.context_limit.toLocaleString() }}</span>
          <span class="context-unit">tokens</span>
        </div>
        <div class="context-bar-track">
          <div
            class="context-bar-fill"
            :style="{
              width: `${Math.min(100, (taskContext.total_tokens / taskContext.context_limit) * 100)}%`,
              background: taskContext.total_tokens / taskContext.context_limit >= 0.8 ? '#dc2626' : taskContext.total_tokens / taskContext.context_limit >= 0.6 ? '#d97706' : '#16a34a',
            }"
          />
        </div>
        <div v-if="taskContext.nodes.length" class="context-nodes">
          <div v-for="node in taskContext.nodes" :key="node.node" class="context-node-row">
            <span class="context-node-name">{{ node.node }}</span>
            <div class="context-node-bar">
              <div
                class="context-node-fill"
                :style="{
                  width: `${Math.min(100, node.utilization * 100)}%`,
                  background: node.utilization >= 0.8 ? '#dc2626' : node.utilization >= 0.6 ? '#d97706' : '#16a34a',
                }"
              />
            </div>
            <span class="context-node-pct">{{ (node.utilization * 100).toFixed(0) }}%</span>
          </div>
        </div>
      </ElCard>

      <div class="cards-grid">
        <ElCard title="回流版本轨迹">
          <EChartPanel :option="reflowTimelineChart" height="280px" empty-text="暂无决策版本轨迹" />
        </ElCard>

        <ElCard title="Reviewer 风险轨迹">
          <EChartPanel :option="reviewTrendChart" height="280px" empty-text="暂无 Reviewer 记录" />
        </ElCard>
      </div>

      <ElCard class="chart-card" title="回流记忆块覆盖">
        <EChartPanel :option="recallChart" height="280px" empty-text="暂无记忆块数据" />
      </ElCard>

      <ElCard v-if="!decisionPackVisible" class="decision-pack-hidden-card" title="决策包未对外展示">
        <p class="decision-pack-hidden-reason">{{ decisionPackHiddenReason }}</p>
      </ElCard>

      <ElCard v-if="decisionPackVisible" class="decision-pack-raw-card" title="原始决策包">
        <pre class="decision-pack-raw">{{ rawDecisionPackText }}</pre>
      </ElCard>

      <div v-if="decisionPackVisible" class="cards-grid">
        <ElCard v-if="positioningActions.length" title="定位建议">
          <div class="action-list">
            <div v-for="(action, index) in positioningActions" :key="index" class="action-item">
              <h4>{{ action.title }}</h4>
              <p class="action-recommendation">{{ action.recommendation }}</p>
              <p class="action-rationale">{{ action.rationale }}</p>
            </div>
          </div>
        </ElCard>

        <ElCard v-if="mvpPriorities.length" title="MVP 优先级">
          <div class="action-list">
            <div v-for="(action, index) in mvpPriorities" :key="index" class="action-item">
              <h4>{{ action.title }}</h4>
              <p class="action-recommendation">{{ action.recommendation }}</p>
              <p class="action-rationale">{{ action.rationale }}</p>
            </div>
          </div>
        </ElCard>

        <ElCard v-if="pricingInsights.length" title="定价洞察">
          <div class="action-list">
            <div v-for="(action, index) in pricingInsights" :key="index" class="action-item">
              <h4>{{ action.title }}</h4>
              <p class="action-recommendation">{{ action.recommendation }}</p>
              <p class="action-rationale">{{ action.rationale }}</p>
            </div>
          </div>
        </ElCard>

        <ElCard v-if="battlecard.length" title="对战卡">
          <div class="action-list">
            <div v-for="(action, index) in battlecard" :key="index" class="action-item">
              <h4>{{ action.title }}</h4>
              <p class="action-recommendation">{{ action.recommendation }}</p>
              <p class="action-rationale">{{ action.rationale }}</p>
            </div>
          </div>
        </ElCard>
      </div>

      <ElCard v-if="memoryItems.length" title="记忆块明细">
        <ElTable :data="memoryItems" border>
          <ElTableColumn prop="id" label="ID" width="120" />
          <ElTableColumn prop="pack_id" label="Pack" width="160" />
          <ElTableColumn label="版本" width="100">
            <template #default="scope">
              {{ scope.row.version ?? '-' }}
            </template>
          </ElTableColumn>
          <ElTableColumn label="相似度" width="120">
            <template #default="scope">
              {{ scope.row.similarity != null ? scope.row.similarity.toFixed(3) : '-' }}
            </template>
          </ElTableColumn>
          <ElTableColumn prop="chunk_type" label="类型" width="120" />
          <ElTableColumn label="来源" min-width="180">
            <template #default="scope">
              {{ (scope.row.source_refs || []).join(', ') || '-' }}
            </template>
          </ElTableColumn>
        </ElTable>
      </ElCard>

      <ElCard v-if="decisionHistory.length" title="决策包历史">
        <ElTable :data="decisionHistory" border>
          <ElTableColumn prop="pack_id" label="Pack ID" width="180" />
          <ElTableColumn label="版本" width="100">
            <template #default="scope">
              {{ scope.row.version ?? '-' }}
            </template>
          </ElTableColumn>
          <ElTableColumn prop="status" label="状态" width="120" />
          <ElTableColumn prop="parent_pack_id" label="父版本" width="180" />
          <ElTableColumn prop="superseded_by" label="替代版本" width="180" />
          <ElTableColumn label="时间" width="180">
            <template #default="scope">
              {{ formatDate(scope.row.created_at) }}
            </template>
          </ElTableColumn>
          <ElTableColumn prop="summary" label="摘要" min-width="260" />
        </ElTable>
      </ElCard>

      <ElCard v-if="evidenceList.length" title="证据列表">
        <ElTable :data="evidenceList" border>
          <ElTableColumn prop="id" label="ID" width="120" />
          <ElTableColumn prop="competitor" label="竞品" width="150" />
          <ElTableColumn prop="dimension" label="维度" width="120" />
          <ElTableColumn prop="claim" label="主张" />
          <ElTableColumn prop="confidence" label="置信度" width="100" />
          <ElTableColumn prop="source_type" label="来源类型" width="120" />
        </ElTable>
      </ElCard>

      <ElCard v-if="events.length" title="事件日志">
        <div class="events-list">
          <div v-for="(event, index) in events" :key="index" class="event-item">
            <span class="event-time">{{ formatDate(event.created_at) }}</span>
            <span class="event-stage">[{{ event.stage }}]</span>
            <span class="event-message">{{ event.message }}</span>
          </div>
        </div>
      </ElCard>
    </template>

    <div v-else class="empty-state">
      <p>未找到任务详情</p>
      <ElButton @click="goBack">返回首页</ElButton>
    </div>
  </div>
</template>

<style scoped>
.task-detail-page {
  max-width: 1440px;
  margin: 0 auto;
  padding: 20px;
}

.page-header {
  display: flex;
  align-items: center;
  gap: 16px;
  margin-bottom: 20px;
}

.back-btn {
  padding: 8px 16px;
  background: #f1f5f9;
  border: none;
  border-radius: 8px;
  cursor: pointer;
  font-size: 14px;
}

.back-btn:hover {
  background: #e2e8f0;
}

.page-header h1 {
  margin: 0;
  font-size: 28px;
}

.page-subtitle {
  margin: 6px 0 0;
  color: #64748b;
}

.loading {
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  padding: 40px;
}

.spinner {
  width: 40px;
  height: 40px;
  border: 4px solid #f3f3f3;
  border-top: 4px solid #2563eb;
  border-radius: 50%;
  animation: spin 1s linear infinite;
}

@keyframes spin {
  0% { transform: rotate(0deg); }
  100% { transform: rotate(360deg); }
}

.task-info-card {
  margin-bottom: 20px;
}

.task-header {
  margin-bottom: 16px;
}

.task-title {
  display: flex;
  align-items: center;
  gap: 12px;
  margin-bottom: 8px;
  flex-wrap: wrap;
}

.task-title h2 {
  margin: 0;
  font-size: 20px;
}

.task-meta {
  display: flex;
  gap: 20px;
  color: #64748b;
  font-size: 14px;
  flex-wrap: wrap;
}

.task-metrics {
  display: grid;
  grid-template-columns: repeat(4, minmax(0, 1fr));
  gap: 16px;
}

.metric-item {
  display: flex;
  flex-direction: column;
  padding: 12px;
  background: #f8fafc;
  border-radius: 12px;
}

.metric-label {
  font-size: 12px;
  color: #64748b;
  margin-bottom: 4px;
}

.metric-value {
  font-size: 20px;
  font-weight: 600;
  color: #0f172a;
}

.metric-value.high-risk {
  color: #ef4444;
}

.task-footer {
  display: flex;
  gap: 12px;
  flex-wrap: wrap;
  margin-top: 16px;
  padding: 12px 14px;
  border-radius: 12px;
  background: #eff6ff;
  color: #1d4ed8;
  font-size: 13px;
}

.cards-grid {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 20px;
  margin-bottom: 20px;
}

.chart-card {
  margin-bottom: 20px;
}

.decision-pack-raw-card {
  margin-bottom: 20px;
}

.decision-pack-raw {
  margin: 0;
  padding: 16px;
  border-radius: 12px;
  background: #0f172a;
  color: #e2e8f0;
  font-size: 12px;
  line-height: 1.6;
  overflow: auto;
  white-space: pre-wrap;
  word-break: break-word;
}

.action-item {
  padding: 12px;
  background: #f8fafc;
  border-radius: 8px;
}

.action-item h4 {
  margin: 0 0 8px 0;
  font-size: 14px;
  color: #1e293b;
}

.action-recommendation {
  margin: 0 0 8px 0;
  font-size: 13px;
  color: #334155;
}

.action-rationale {
  margin: 0;
  font-size: 12px;
  color: #64748b;
  font-style: italic;
}

.events-list {
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.event-item {
  display: flex;
  gap: 12px;
  padding: 8px;
  background: #f8fafc;
  border-radius: 6px;
  font-size: 13px;
}

.event-time {
  color: #94a3b8;
  min-width: 140px;
}

.event-stage {
  color: #2563eb;
  font-weight: 600;
  min-width: 80px;
}

.event-message {
  color: #334155;
}

.empty-state {
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  padding: 40px;
}

@media (max-width: 1100px) {
  .task-metrics,
  .cards-grid {
    grid-template-columns: 1fr;
  }

  .task-meta,
  .task-footer {
    flex-direction: column;
    gap: 8px;
  }
}

/* 记忆状态面板 */
.memory-panel {
  margin-bottom: 16px;
}

.memory-grid {
  display: flex;
  gap: 24px;
  flex-wrap: wrap;
}

.memory-cell {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 4px;
}

.memory-value {
  font-size: 24px;
  font-weight: 700;
  color: #2563eb;
}

.memory-label {
  font-size: 12px;
  color: #64748b;
}

.memory-summary {
  margin-top: 12px;
  padding-top: 12px;
  border-top: 1px solid rgba(148, 163, 184, 0.15);
}

.memory-summary p {
  margin: 4px 0 0;
  font-size: 13px;
  color: #334155;
  line-height: 1.5;
}

.memory-retry {
  margin-top: 8px;
}

/* 上下文监控面板 */
.context-panel {
  margin-bottom: 16px;
}

.context-overview {
  display: flex;
  align-items: baseline;
  gap: 6px;
  margin-bottom: 10px;
}

.context-total {
  font-size: 24px;
  font-weight: 700;
  color: #0f172a;
}

.context-sep {
  font-size: 16px;
  color: #94a3b8;
}

.context-limit {
  font-size: 16px;
  color: #64748b;
}

.context-unit {
  font-size: 12px;
  color: #94a3b8;
}

.context-bar-track {
  height: 8px;
  background: #f1f5f9;
  border-radius: 4px;
  overflow: hidden;
  margin-bottom: 16px;
}

.context-bar-fill {
  height: 100%;
  border-radius: 4px;
  transition: width 0.3s ease;
}

.context-nodes {
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.context-node-row {
  display: flex;
  align-items: center;
  gap: 10px;
}

.context-node-name {
  font-size: 13px;
  font-weight: 600;
  color: #0f172a;
  min-width: 80px;
}

.context-node-bar {
  flex: 1;
  height: 6px;
  background: #f1f5f9;
  border-radius: 3px;
  overflow: hidden;
}

.context-node-fill {
  height: 100%;
  border-radius: 3px;
  transition: width 0.3s ease;
}

.context-node-pct {
  font-size: 12px;
  font-weight: 600;
  color: #475569;
  min-width: 40px;
  text-align: right;
}
</style>