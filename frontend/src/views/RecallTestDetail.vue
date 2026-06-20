<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue';
import { useRoute, useRouter } from 'vue-router';
import { ElButton, ElCard, ElDivider, ElInput, ElMessage, ElOption, ElSelect, ElTable, ElTableColumn, ElTag } from 'element-plus';
import type { EChartsOption } from 'echarts';

import EChartPanel from '../components/EChartPanel.vue';
import { compareRecallTests, getRecallTestResult, type RecallCompareResult, type RecallTestResult } from '../services/api';

const route = useRoute();
const router = useRouter();

const loading = ref(false);
const currentResult = ref<RecallTestResult | null>(null);
const compareResult = ref<RecallCompareResult | null>(null);
const compareTarget = ref('');
const activeMode = ref('hybrid');

const testId = computed(() => route.params.testId as string);

const modeOptions = computed(() => {
  const modes = currentResult.value?.modes ?? [];
  return modes.length > 0 ? modes : ['hybrid'];
});

const summaryRows = computed(() => Object.entries(currentResult.value?.summary ?? {}).map(([mode, metrics]) => ({
  mode,
  metrics,
})));

const byCategoryRows = computed(() => Object.entries(currentResult.value?.by_category ?? {}).map(([category, metrics]) => ({
  category,
  metrics: (metrics as Record<string, Record<string, number>>)[activeMode.value] ?? null,
})));

const detailRows = computed(() => currentResult.value?.results?.filter((row) => row.mode === activeMode.value) ?? []);

const currentSummary = computed(() => currentResult.value?.aggregated_metrics ?? null);
const benchmarkMeta = computed(() => ({
  dataset_id: currentResult.value?.dataset_id ?? '-',
  dataset_version: currentResult.value?.config?.dataset_version ?? '-',
  top_k: currentResult.value?.config?.top_k ?? 5,
  total_cases: currentResult.value?.config?.total_cases ?? 0,
  primary_mode: currentResult.value?.primary_mode ?? activeMode.value,
}));

const qualityChart = computed<EChartsOption | null>(() => {
  const metrics = currentSummary.value;
  if (!metrics) return null;

  return {
    grid: { left: 40, right: 24, top: 32, bottom: 24, containLabel: true },
    tooltip: { trigger: 'axis' },
    xAxis: { type: 'category', data: ['recall@5', 'ndcg@5', 'mrr'] },
    yAxis: { type: 'value', max: 1 },
    series: [
      {
        type: 'bar',
        name: '当前测试',
        data: [metrics.recall_at_k, metrics.ndcg_at_k, metrics.mrr_at_k],
        itemStyle: { color: '#2563eb' },
      },
    ],
  };
});

const latencyChart = computed<EChartsOption | null>(() => {
  const metrics = currentSummary.value;
  if (!metrics) return null;

  return {
    grid: { left: 40, right: 24, top: 32, bottom: 24, containLabel: true },
    tooltip: { trigger: 'axis' },
    xAxis: { type: 'category', data: ['avg_latency_ms'] },
    yAxis: { type: 'value' },
    series: [
      {
        type: 'bar',
        name: '延迟',
        data: [metrics.avg_latency_ms],
        itemStyle: { color: '#f59e0b' },
      },
    ],
  };
});

const compareQualityChart = computed<EChartsOption | null>(() => {
  const diff = compareResult.value?.diff?.[activeMode.value];
  if (!diff) return null;

  return {
    grid: { left: 40, right: 24, top: 32, bottom: 24, containLabel: true },
    tooltip: { trigger: 'axis' },
    legend: { top: 0 },
    xAxis: { type: 'category', data: ['recall@5', 'ndcg@5', 'mrr'] },
    yAxis: { type: 'value', max: 1 },
    series: [
      {
        type: 'bar',
        name: 'baseline',
        data: [diff['recall@5']?.a ?? 0, diff['ndcg@5']?.a ?? 0, diff.mrr?.a ?? 0],
        itemStyle: { color: '#94a3b8' },
      },
      {
        type: 'bar',
        name: 'optimized',
        data: [diff['recall@5']?.b ?? 0, diff['ndcg@5']?.b ?? 0, diff.mrr?.b ?? 0],
        itemStyle: { color: '#16a34a' },
      },
    ],
  };
});

const compareLatencyChart = computed<EChartsOption | null>(() => {
  const diff = compareResult.value?.diff?.[activeMode.value];
  if (!diff) return null;

  return {
    grid: { left: 40, right: 24, top: 32, bottom: 24, containLabel: true },
    tooltip: { trigger: 'axis' },
    legend: { top: 0 },
    xAxis: { type: 'category', data: ['avg_latency_ms'] },
    yAxis: { type: 'value' },
    series: [
      {
        type: 'bar',
        name: 'baseline',
        data: [diff.avg_latency_ms?.a ?? 0],
        itemStyle: { color: '#94a3b8' },
      },
      {
        type: 'bar',
        name: 'optimized',
        data: [diff.avg_latency_ms?.b ?? 0],
        itemStyle: { color: '#f59e0b' },
      },
    ],
  };
});

function formatDate(value?: string) {
  if (!value) return '-';
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? value : date.toLocaleString('zh-CN');
}

function metricText(value?: number, digits = 4) {
  if (typeof value !== 'number' || Number.isNaN(value)) return '-';
  return value.toFixed(digits);
}

function metricDelta(metric?: { a: number; b: number; delta: number }, digits = 4) {
  if (!metric) return '-';
  const sign = metric.delta >= 0 ? '+' : '';
  return `${metric.a.toFixed(digits)} → ${metric.b.toFixed(digits)} (${sign}${metric.delta.toFixed(digits)})`;
}

function getModeSummary(mode: string) {
  return currentResult.value?.summary_by_mode?.[mode] ?? currentResult.value?.summary?.[mode] ?? null;
}

function normalizeMode() {
  const modes = modeOptions.value;
  if (!modes.includes(activeMode.value)) {
    activeMode.value = modes[0] ?? 'hybrid';
  }
}

async function loadResult() {
  loading.value = true;
  compareResult.value = null;
  try {
    currentResult.value = await getRecallTestResult(testId.value);
    const modes = currentResult.value?.modes ?? [];
    activeMode.value = modes.includes('hybrid') ? 'hybrid' : (modes[0] ?? 'hybrid');
    normalizeMode();
    compareTarget.value = '';
  } catch (error) {
    ElMessage.error(error instanceof Error ? error.message : '获取召回详情失败');
  } finally {
    loading.value = false;
  }
}

async function runCompare() {
  if (!compareTarget.value) {
    ElMessage.warning('请先输入对比目标 test_id');
    return;
  }
  compareResult.value = null;
  try {
    compareResult.value = await compareRecallTests(testId.value, compareTarget.value);
    ElMessage.success('对比完成');
  } catch (error) {
    ElMessage.error(error instanceof Error ? error.message : '对比失败');
  }
}

function goBack() {
  router.push('/recall');
}

watch(testId, () => {
  loadResult();
});

watch(activeMode, () => {
  if (currentResult.value?.modes?.includes(activeMode.value)) return;
  normalizeMode();
});

onMounted(() => {
  loadResult();
});
</script>

<template>
  <div class="recall-detail-page">
    <div class="page-header">
      <button class="back-btn" @click="goBack">← 返回召回页</button>
      <div>
        <h1>召回测试详情</h1>
        <p class="page-subtitle">test_id: {{ testId }}</p>
      </div>
    </div>

    <div v-if="loading" class="loading-state">
      <div class="spinner"></div>
      <p>加载中...</p>
    </div>

    <template v-else-if="currentResult">
      <ElCard class="meta-card">
        <div class="meta-top">
          <div>
            <div class="meta-kicker">Benchmark</div>
            <h2>{{ currentResult.dataset_id }}</h2>
            <p>
              {{ currentResult.config.description }}
            </p>
          </div>
          <div class="meta-tags">
            <ElTag type="success">{{ currentResult.config.dataset_version }}</ElTag>
            <ElTag type="info">top_k={{ currentResult.config.top_k }}</ElTag>
            <ElTag>{{ benchmarkMeta.primary_mode }}</ElTag>
          </div>
        </div>

        <ElDivider />

        <div class="kpi-grid">
          <div class="kpi-card">
            <span>recall@5</span>
            <strong>{{ metricText(currentSummary?.recall_at_k, 4) }}</strong>
          </div>
          <div class="kpi-card">
            <span>ndcg@5</span>
            <strong>{{ metricText(currentSummary?.ndcg_at_k, 4) }}</strong>
          </div>
          <div class="kpi-card">
            <span>mrr</span>
            <strong>{{ metricText(currentSummary?.mrr_at_k, 4) }}</strong>
          </div>
          <div class="kpi-card">
            <span>avg_latency_ms</span>
            <strong>{{ metricText(currentSummary?.avg_latency_ms, 2) }}</strong>
          </div>
        </div>

        <div class="meta-stats">
          <div>
            <span>模式数</span>
            <strong>{{ currentResult.modes.length }}</strong>
          </div>
          <div>
            <span>测试用例</span>
            <strong>{{ benchmarkMeta.total_cases }}</strong>
          </div>
          <div>
            <span>执行时长</span>
            <strong>{{ metricText(currentResult.duration_ms / 1000, 2) }}s</strong>
          </div>
          <div>
            <span>主模式</span>
            <strong>{{ currentResult.primary_mode ?? 'hybrid' }}</strong>
          </div>
        </div>
      </ElCard>

      <ElCard class="control-card">
        <div class="control-row">
          <div class="mode-group">
            <span class="section-label">查看模式</span>
            <ElSelect v-model="activeMode" class="mode-select">
              <ElOption v-for="mode in modeOptions" :key="mode" :label="mode" :value="mode" />
            </ElSelect>
          </div>

          <div class="compare-group">
            <span class="section-label">对比另一条历史</span>
            <div class="compare-row">
              <ElInput v-model="compareTarget" placeholder="输入 baseline / optimized 的 test_id" />
              <ElButton type="primary" @click="runCompare">执行对比</ElButton>
            </div>
          </div>
        </div>
      </ElCard>

      <div class="chart-grid">
        <ElCard title="指标概览">
          <EChartPanel :option="qualityChart" height="280px" empty-text="暂无质量图" />
        </ElCard>
        <ElCard title="延迟概览">
          <EChartPanel :option="latencyChart" height="280px" empty-text="暂无延迟图" />
        </ElCard>
      </div>

      <ElCard title="按模式汇总">
        <ElTable :data="summaryRows" border>
          <ElTableColumn prop="mode" label="模式" width="160" />
          <ElTableColumn label="recall@5" width="140">
            <template #default="scope">
              {{ metricText(scope.row.metrics.recall_at_k, 4) }}
            </template>
          </ElTableColumn>
          <ElTableColumn label="ndcg@5" width="140">
            <template #default="scope">
              {{ metricText(scope.row.metrics.ndcg_at_k, 4) }}
            </template>
          </ElTableColumn>
          <ElTableColumn label="mrr" width="140">
            <template #default="scope">
              {{ metricText(scope.row.metrics.mrr_at_k, 4) }}
            </template>
          </ElTableColumn>
          <ElTableColumn label="avg_latency_ms" width="160">
            <template #default="scope">
              {{ metricText(scope.row.metrics.avg_latency_ms, 2) }}
            </template>
          </ElTableColumn>
        </ElTable>
      </ElCard>

      <ElCard title="分桶分析">
        <ElTable :data="byCategoryRows" border>
          <ElTableColumn prop="category" label="场景" width="180" />
          <ElTableColumn label="recall@5" width="140">
            <template #default="scope">
              {{ metricText(scope.row.metrics?.['recall@5'], 4) }}
            </template>
          </ElTableColumn>
          <ElTableColumn label="ndcg@5" width="140">
            <template #default="scope">
              {{ metricText(scope.row.metrics?.['ndcg@5'], 4) }}
            </template>
          </ElTableColumn>
          <ElTableColumn label="mrr" width="140">
            <template #default="scope">
              {{ metricText(scope.row.metrics?.mrr, 4) }}
            </template>
          </ElTableColumn>
          <ElTableColumn label="avg_latency_ms" width="160">
            <template #default="scope">
              {{ metricText(scope.row.metrics?.avg_latency_ms, 2) }}
            </template>
          </ElTableColumn>
        </ElTable>
      </ElCard>

      <ElCard title="Case 明细">
        <ElTable :data="detailRows" border>
          <ElTableColumn prop="case_id" label="Case ID" width="120" />
          <ElTableColumn prop="query" label="查询" min-width="180" />
          <ElTableColumn label="命中" width="100">
            <template #default="scope">
              <ElTag :type="scope.row.metrics.recall_at_k > 0 ? 'success' : 'danger'">
                {{ scope.row.metrics.recall_at_k > 0 ? '命中' : '未命中' }}
              </ElTag>
            </template>
          </ElTableColumn>
          <ElTableColumn label="召回率" width="120">
            <template #default="scope">
              {{ metricText(scope.row.metrics.recall_at_k, 4) }}
            </template>
          </ElTableColumn>
          <ElTableColumn label="NDCG" width="120">
            <template #default="scope">
              {{ metricText(scope.row.metrics.ndcg_at_k, 4) }}
            </template>
          </ElTableColumn>
          <ElTableColumn label="MRR" width="120">
            <template #default="scope">
              {{ metricText(scope.row.metrics.mrr_at_k, 4) }}
            </template>
          </ElTableColumn>
          <ElTableColumn label="期望 ID" min-width="180">
            <template #default="scope">
              {{ scope.row.expected_ids.join(', ') }}
            </template>
          </ElTableColumn>
          <ElTableColumn label="命中结果" min-width="220">
            <template #default="scope">
              {{ scope.row.retrieved_ids.join(', ') }}
            </template>
          </ElTableColumn>
        </ElTable>
      </ElCard>

      <div v-if="compareResult" class="compare-section">
        <ElCard title="对比结果">
          <div class="compare-head">
            <div>
              <div class="meta-kicker">Baseline</div>
              <strong>{{ compareResult.test_a.test_id }}</strong>
            </div>
            <div>
              <div class="meta-kicker">Optimized</div>
              <strong>{{ compareResult.test_b.test_id }}</strong>
            </div>
            <div v-if="compareResult.diff[activeMode]">
              <div class="meta-kicker">recall@5</div>
              <strong>{{ metricDelta(compareResult.diff[activeMode]['recall@5'], 4) }}</strong>
            </div>
            <div v-if="compareResult.diff[activeMode]">
              <div class="meta-kicker">avg_latency_ms</div>
              <strong>{{ metricDelta(compareResult.diff[activeMode].avg_latency_ms, 2) }}</strong>
            </div>
          </div>

          <div class="chart-grid">
            <EChartPanel :option="compareQualityChart" height="260px" empty-text="暂无对比质量图" />
            <EChartPanel :option="compareLatencyChart" height="260px" empty-text="暂无对比延迟图" />
          </div>
        </ElCard>

        <ElCard v-if="compareResult.by_category_diff" title="分桶对比">
          <ElTable :data="Object.entries(compareResult.by_category_diff)" border>
            <ElTableColumn prop="0" label="场景" width="180" />
            <ElTableColumn label="recall@5" width="180">
              <template #default="scope">
                {{ metricDelta(scope.row[1]?.[activeMode]?.['recall@5']) }}
              </template>
            </ElTableColumn>
            <ElTableColumn label="ndcg@5" width="180">
              <template #default="scope">
                {{ metricDelta(scope.row[1]?.[activeMode]?.['ndcg@5']) }}
              </template>
            </ElTableColumn>
            <ElTableColumn label="avg_latency_ms" width="200">
              <template #default="scope">
                {{ metricDelta(scope.row[1]?.[activeMode]?.avg_latency_ms, 2) }}
              </template>
            </ElTableColumn>
          </ElTable>
        </ElCard>
      </div>

      <ElCard title="原始摘要">
        <ElTable :data="summaryRows" border>
          <ElTableColumn prop="mode" label="模式" width="160" />
          <ElTableColumn label="summary 结构" min-width="260">
            <template #default="scope">
              {{ JSON.stringify(scope.row.metrics) }}
            </template>
          </ElTableColumn>
        </ElTable>
      </ElCard>
    </template>

    <div v-else class="empty-state">
      <p>未找到召回测试结果</p>
      <ElButton @click="goBack">返回</ElButton>
    </div>
  </div>
</template>

<style scoped>
.recall-detail-page {
  max-width: 1440px;
  margin: 0 auto;
  padding: 20px;
}

.page-header {
  display: flex;
  gap: 16px;
  align-items: center;
  margin-bottom: 18px;
}

.page-header h1 {
  margin: 0;
  font-size: 28px;
}

.page-subtitle {
  margin: 6px 0 0;
  color: #64748b;
}

.back-btn {
  padding: 8px 16px;
  border: none;
  border-radius: 10px;
  background: #f1f5f9;
  cursor: pointer;
}

.loading-state,
.empty-state {
  min-height: 300px;
  display: grid;
  place-items: center;
  color: #64748b;
}

.spinner {
  width: 34px;
  height: 34px;
  border: 4px solid #e2e8f0;
  border-top-color: #2563eb;
  border-radius: 999px;
  animation: spin 1s linear infinite;
}

.meta-card,
.control-card,
.compare-section {
  margin-bottom: 20px;
}

.meta-top,
.control-row,
.compare-head {
  display: flex;
  gap: 16px;
  justify-content: space-between;
  align-items: flex-start;
}

.meta-top h2 {
  margin: 8px 0 6px;
}

.meta-top p {
  margin: 0;
  color: #64748b;
}

.meta-kicker,
.section-label {
  color: #2563eb;
  font-size: 12px;
  font-weight: 700;
  letter-spacing: 0.08em;
  text-transform: uppercase;
}

.meta-tags {
  display: flex;
  gap: 8px;
  flex-wrap: wrap;
}

.kpi-grid,
.chart-grid {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 14px;
}

.kpi-card {
  padding: 14px;
  border-radius: 14px;
  background: linear-gradient(180deg, #f8fafc 0%, #ffffff 100%);
  border: 1px solid rgba(148, 163, 184, 0.18);
  display: grid;
  gap: 6px;
}

.kpi-card span,
.compare-head .meta-kicker {
  font-size: 12px;
  color: #64748b;
}

.kpi-card strong,
.compare-head strong,
.meta-stats strong {
  font-size: 20px;
  color: #0f172a;
}

.meta-stats {
  display: grid;
  grid-template-columns: repeat(4, minmax(0, 1fr));
  gap: 12px;
  margin-top: 16px;
}

.meta-stats > div {
  padding: 12px;
  background: #f8fafc;
  border-radius: 14px;
  display: grid;
  gap: 6px;
}

.mode-group,
.compare-group {
  display: grid;
  gap: 8px;
}

.mode-select {
  min-width: 220px;
}

.compare-row {
  display: flex;
  gap: 10px;
}

.compare-row :deep(.el-input) {
  min-width: 320px;
}

.compare-section {
  display: grid;
  gap: 20px;
}

.empty-state p {
  margin: 0 0 12px;
}

@keyframes spin {
  to {
    transform: rotate(360deg);
  }
}

@media (max-width: 1100px) {
  .kpi-grid,
  .chart-grid,
  .meta-stats {
    grid-template-columns: 1fr;
  }

  .meta-top,
  .control-row,
  .compare-head,
  .compare-row {
    flex-direction: column;
  }

  .compare-row :deep(.el-input),
  .mode-select {
    min-width: 0;
    width: 100%;
  }
}
</style>