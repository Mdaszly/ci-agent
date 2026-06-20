<script setup lang="ts">
import { computed, onMounted, reactive, ref, watch } from 'vue';
import { useRouter } from 'vue-router';
import { ElButton, ElCard, ElDivider, ElForm, ElFormItem, ElInput, ElMessage, ElOption, ElSelect, ElTable, ElTableColumn, ElTag } from 'element-plus';
import type { EChartsOption } from 'echarts';

import EChartPanel from '../components/EChartPanel.vue';
import {
  compareRecallTests,
  createRecallDataset,
  deleteRecallTest,
  getRecallHistory,
  runRecallTest,
  type RecallCompareResult,
  type RecallTestDataset,
  type RecallTestHistory,
  type RecallTestRequest,
  type RecallTestResult,
} from '../services/api';

const router = useRouter();

const benchmarkMeta = {
  dataset_id: 'recall_benchmark_v1',
  dataset_version: 'v1.0.0',
  description: '固定评测集，基准口径为 hybrid + top_k=5',
};

const loading = ref(false);
const historyLoading = ref(false);
const compareLoading = ref(false);
const testLoading = ref(false);
const currentResult = ref<RecallTestResult | null>(null);
const history = ref<RecallTestHistory[]>([]);
const compareResult = ref<RecallCompareResult | null>(null);
const datasetFilter = ref('');

const datasetForm = reactive({
  dataset_id: benchmarkMeta.dataset_id,
  dataset_version: benchmarkMeta.dataset_version,
  description: '本地手工测试集',
  memory_items: [
    { id: 'm1', content: '几素风扇风力强劲噪音大', competitor: '几素' },
    { id: 'm2', content: '铁布衫风扇静音效果好', competitor: '铁布衫' },
    { id: 'm3', content: '几素风扇续航8小时', competitor: '几素' },
    { id: 'm4', content: '铁布衫风扇价格79元', competitor: '铁布衫' },
  ],
  test_cases: [
    { case_id: 'c1', query: '噪音控制', expected_ids_str: 'm2' },
    { case_id: 'c2', query: '风力', expected_ids_str: 'm1' },
    { case_id: 'c3', query: '续航', expected_ids_str: 'm3' },
    { case_id: 'c4', query: '价格', expected_ids_str: 'm4' },
  ],
});

const testForm = reactive({
  dataset_id: benchmarkMeta.dataset_id,
  modes: ['hybrid'],
  top_k: 5,
  vector_weight: 0.7,
  lexical_weight: 0.3,
  fusion_strategy: 'weighted',
  candidate_multiplier: 2,
  hnsw_ef_search: 40,
  allow_degraded_mode: true,
});

const compareForm = reactive({
  test_a: '',
  test_b: '',
});

const modeOptions = [
  { value: 'vector_only', label: '向量检索' },
  { value: 'lexical_only', label: '词面检索' },
  { value: 'hybrid', label: '混合检索' },
];

const strategyOptions = [
  { value: 'weighted', label: '线性加权' },
  { value: 'rrf', label: 'RRF 融合' },
];

const datasetOptions = computed(() => {
  const map = new Map<string, { value: string; label: string }>();
  history.value.forEach((item) => {
    const key = `${item.dataset_id}::${item.dataset_version}`;
    if (!map.has(key)) {
      map.set(key, {
        value: item.dataset_id,
        label: `${item.dataset_id} · ${item.dataset_version}`,
      });
    }
  });
  return Array.from(map.values());
});

const historyOptions = computed(() => history.value.map((item) => ({
  value: item.test_id,
  label: formatCompareItemLabel(item),
})));

const currentHybridSummary = computed(() => currentResult.value?.summary?.hybrid ?? null);

const selectedHistory = computed(() => history.value.find((item) => item.test_id === compareForm.test_a) ?? null);

function formatDate(value: string) {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return date.toLocaleString('zh-CN', {
    year: 'numeric',
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
    second: '2-digit',
  });
}

function metricText(value?: number, digits = 4) {
  if (typeof value !== 'number' || Number.isNaN(value)) return '-';
  return value.toFixed(digits);
}

function metricDeltaText(metric?: { a: number; b: number; delta: number }, digits = 4) {
  if (!metric) return '-';
  const sign = metric.delta >= 0 ? '+' : '';
  return `${metric.a.toFixed(digits)} → ${metric.b.toFixed(digits)} (${sign}${metric.delta.toFixed(digits)})`;
}

function formatRunShortId(testId: string) {
  const parts = testId.split('_');
  return parts.length >= 3 ? parts[2] : testId;
}

function formatFusionStrategy(strategy: string) {
  const labelMap: Record<string, string> = {
    weighted: '加权',
    rrf: 'RRF',
  };
  return labelMap[strategy] ?? strategy;
}

function formatCompareItemLabel(item: RecallTestHistory) {
  return `${formatRunShortId(item.test_id)} · ${item.dataset_id} · ${formatFusionStrategy(item.fusion_strategy)}`;
}

function clampHistory(historyItems: RecallTestHistory[], size = 8) {
  return [...historyItems].slice(0, size).reverse();
}

const currentQualityChart = computed<EChartsOption | null>(() => {
  const metrics = currentResult.value?.aggregated_metrics;
  if (!metrics) return null;

  return {
    grid: { left: 36, right: 24, top: 32, bottom: 24, containLabel: true },
    tooltip: { trigger: 'axis' },
    xAxis: {
      type: 'category',
      data: ['recall@5', 'ndcg@5', 'mrr'],
    },
    yAxis: {
      type: 'value',
      max: 1,
    },
    series: [
      {
        type: 'bar',
        name: '当前结果',
        data: [metrics.recall_at_k, metrics.ndcg_at_k, metrics.mrr_at_k],
        itemStyle: { color: '#2563eb' },
      },
    ],
  };
});

const currentLatencyChart = computed<EChartsOption | null>(() => {
  const metrics = currentResult.value?.aggregated_metrics;
  if (!metrics) return null;

  return {
    grid: { left: 36, right: 24, top: 32, bottom: 24, containLabel: true },
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
  const diff = compareResult.value?.diff.hybrid;
  if (!diff) return null;

  return {
    grid: { left: 36, right: 24, top: 32, bottom: 24, containLabel: true },
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
  const diff = compareResult.value?.diff.hybrid;
  if (!diff) return null;

  return {
    grid: { left: 36, right: 24, top: 32, bottom: 24, containLabel: true },
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

const historyTrendChart = computed<EChartsOption | null>(() => {
  const rows = clampHistory(history.value, 10);
  if (rows.length === 0) return null;

  return {
    grid: { left: 36, right: 24, top: 32, bottom: 44, containLabel: true },
    tooltip: { trigger: 'axis' },
    xAxis: { type: 'category', data: rows.map((item) => item.test_id) },
    yAxis: { type: 'value', max: 1 },
    dataZoom: rows.length > 6 ? [{ type: 'inside' }, { type: 'slider', height: 18, bottom: 6 }] : [],
    series: [
      {
        type: 'line',
        name: 'recall@5',
        data: rows.map((item) => item['hybrid_recall@5']),
        smooth: true,
        symbolSize: 8,
        itemStyle: { color: '#2563eb' },
        areaStyle: { opacity: 0.12 },
      },
    ],
  };
});

async function loadHistory() {
  historyLoading.value = true;
  try {
    history.value = await getRecallHistory(20, datasetFilter.value || undefined);
  } catch (error) {
    ElMessage.error(error instanceof Error ? error.message : '获取历史记录失败');
  } finally {
    historyLoading.value = false;
  }
}

async function saveDataset() {
  loading.value = true;
  try {
    const dataset: RecallTestDataset = {
      dataset_id: datasetForm.dataset_id,
      dataset_version: datasetForm.dataset_version,
      description: datasetForm.description,
      baseline_config: {
        top_k: 5,
        vector_weight: 0.7,
        lexical_weight: 0.3,
        fusion_strategy: 'weighted',
        candidate_multiplier: 2,
        hnsw_ef_search: 40,
      },
      clear_existing: true,
      memory_items: datasetForm.memory_items.map((item) => ({
        id: item.id,
        task_id: datasetForm.dataset_id,
        pack_id: `${datasetForm.dataset_id}_pack`,
        version: 1,
        chunk_type: 'decision',
        stage: 'manual',
        iteration: 0,
        source_refs: item.competitor ? [item.competitor] : [],
        summary: item.content,
        embedding_text: item.content,
        payload: { competitor: item.competitor },
        status: 'approved',
      })),
      test_cases: datasetForm.test_cases.map((item, index) => ({
        case_id: item.case_id,
        category: 'manual',
        bucket: 'manual',
        query: item.query,
        expected_ids: item.expected_ids_str.split(',').map((part) => part.trim()).filter(Boolean),
        description: `manual_case_${index + 1}`,
      })),
    };

    const result = await createRecallDataset(dataset);
    ElMessage.success(result.message);
    await loadHistory();
  } catch (error) {
    ElMessage.error(error instanceof Error ? error.message : '保存数据集失败');
  } finally {
    loading.value = false;
  }
}

async function runTest() {
  testLoading.value = true;
  try {
    const request: RecallTestRequest = {
      dataset_id: testForm.dataset_id,
      modes: testForm.modes,
      top_k: testForm.top_k,
      vector_weight: testForm.vector_weight,
      lexical_weight: testForm.lexical_weight,
      fusion_strategy: testForm.fusion_strategy,
      candidate_multiplier: testForm.candidate_multiplier,
      hnsw_ef_search: testForm.hnsw_ef_search,
      allow_degraded_mode: testForm.allow_degraded_mode,
      detailed: true,
    };
    currentResult.value = await runRecallTest(request);
    compareResult.value = null;
    ElMessage.success('测试完成');
    await loadHistory();
  } catch (error) {
    ElMessage.error(error instanceof Error ? error.message : '执行测试失败');
  } finally {
    testLoading.value = false;
  }
}

async function runComparison() {
  if (!compareForm.test_a || !compareForm.test_b) {
    ElMessage.warning('先选择两条历史记录再对比');
    return;
  }
  compareLoading.value = true;
  try {
    compareResult.value = await compareRecallTests(compareForm.test_a, compareForm.test_b);
    ElMessage.success('对比完成');
  } catch (error) {
    ElMessage.error(error instanceof Error ? error.message : '对比失败');
  } finally {
    compareLoading.value = false;
  }
}

async function handleDelete(testId: string) {
  if (!window.confirm('确定要删除这条测试记录吗？')) return;
  try {
    await deleteRecallTest(testId);
    ElMessage.success('删除成功');
    await loadHistory();
  } catch (error) {
    ElMessage.error(error instanceof Error ? error.message : '删除失败');
  }
}

function openDetail(testId: string) {
  router.push(`/recall/${testId}`);
}

function goBack() {
  router.push('/');
}

function addMemoryItem() {
  datasetForm.memory_items.push({
    id: `m${datasetForm.memory_items.length + 1}`,
    content: '',
    competitor: '',
  });
}

function removeMemoryItem(index: number) {
  if (datasetForm.memory_items.length > 1) {
    datasetForm.memory_items.splice(index, 1);
  }
}

function addTestCase() {
  datasetForm.test_cases.push({
    case_id: `c${datasetForm.test_cases.length + 1}`,
    query: '',
    expected_ids_str: '',
  });
}

function removeTestCase(index: number) {
  if (datasetForm.test_cases.length > 1) {
    datasetForm.test_cases.splice(index, 1);
  }
}

watch(datasetFilter, () => {
  loadHistory();
});

onMounted(async () => {
  await loadHistory();
});
</script>

<template>
  <div class="recall-test-page">
    <div class="page-header">
      <button class="back-btn" @click="goBack">← 返回</button>
      <div>
        <h1>召回率评测</h1>
        <p class="page-subtitle">
          固定测试集：{{ benchmarkMeta.dataset_id }} / {{ benchmarkMeta.dataset_version }}
        </p>
      </div>
    </div>

    <ElCard class="benchmark-banner">
      <div class="banner-main">
        <div>
          <div class="banner-kicker">评测集</div>
          <h2>{{ benchmarkMeta.dataset_id }}</h2>
          <p>{{ benchmarkMeta.description }}</p>
        </div>
        <ElTag type="success">{{ benchmarkMeta.dataset_version }}</ElTag>
      </div>
      <div class="banner-actions">
        <ElSelect v-model="datasetFilter" clearable placeholder="筛选历史评测集" class="dataset-select">
          <ElOption v-for="item in datasetOptions" :key="item.value" :label="item.label" :value="item.value" />
        </ElSelect>
        <ElButton @click="loadHistory">刷新历史</ElButton>
      </div>
    </ElCard>

    <div class="page-content">
      <div class="left-panel">
        <ElCard title="数据集编辑">
          <ElForm :model="datasetForm" label-width="92px">
            <ElFormItem label="评测集 ID">
              <ElInput v-model="datasetForm.dataset_id" />
            </ElFormItem>
            <ElFormItem label="版本">
              <ElInput v-model="datasetForm.dataset_version" />
            </ElFormItem>
            <ElFormItem label="说明">
              <ElInput v-model="datasetForm.description" type="textarea" :rows="2" />
            </ElFormItem>

            <ElFormItem label="记忆块">
              <div class="item-stack">
                <div v-for="(item, index) in datasetForm.memory_items" :key="item.id" class="inline-row">
                  <ElInput v-model="item.id" class="short-input" placeholder="ID" />
                  <ElInput v-model="item.content" class="grow-input" placeholder="内容" />
                  <ElInput v-model="item.competitor" class="short-input" placeholder="竞品" />
                  <button v-if="datasetForm.memory_items.length > 1" class="remove-btn" @click="removeMemoryItem(index)">×</button>
                </div>
                <ElButton link type="primary" @click="addMemoryItem">+ 添加记忆块</ElButton>
              </div>
            </ElFormItem>

            <ElFormItem label="测试用例">
              <div class="item-stack">
                <div v-for="(item, index) in datasetForm.test_cases" :key="item.case_id" class="inline-row">
                  <ElInput v-model="item.case_id" class="short-input" placeholder="Case ID" />
                  <ElInput v-model="item.query" class="grow-input" placeholder="查询词" />
                  <ElInput v-model="item.expected_ids_str" class="grow-input" placeholder="期望 ID，逗号分隔" />
                  <button v-if="datasetForm.test_cases.length > 1" class="remove-btn" @click="removeTestCase(index)">×</button>
                </div>
                <ElButton link type="primary" @click="addTestCase">+ 添加测试用例</ElButton>
              </div>
            </ElFormItem>

            <ElFormItem>
              <ElButton type="primary" :loading="loading" @click="saveDataset">保存数据集</ElButton>
            </ElFormItem>
          </ElForm>
        </ElCard>

        <ElCard title="测试参数">
          <ElForm :model="testForm" label-width="92px">
            <ElFormItem label="评测集 ID">
              <ElInput v-model="testForm.dataset_id" />
            </ElFormItem>
            <ElFormItem label="模式">
              <ElSelect v-model="testForm.modes" multiple class="full-width">
                <ElOption v-for="mode in modeOptions" :key="mode.value" :label="mode.label" :value="mode.value" />
              </ElSelect>
            </ElFormItem>
            <ElFormItem label="Top-K">
              <ElInput v-model.number="testForm.top_k" type="number" min="1" max="20" />
            </ElFormItem>
            <ElFormItem label="融合策略">
              <ElSelect v-model="testForm.fusion_strategy" class="full-width">
                <ElOption v-for="strategy in strategyOptions" :key="strategy.value" :label="strategy.label" :value="strategy.value" />
              </ElSelect>
            </ElFormItem>
            <ElFormItem label="向量权重">
              <ElInput v-model.number="testForm.vector_weight" type="number" min="0" max="1" step="0.1" />
            </ElFormItem>
            <ElFormItem label="词面权重">
              <ElInput v-model.number="testForm.lexical_weight" type="number" min="0" max="1" step="0.1" />
            </ElFormItem>
            <ElFormItem label="候选倍数">
              <ElInput v-model.number="testForm.candidate_multiplier" type="number" min="1" max="10" step="1" />
            </ElFormItem>
            <ElFormItem label="ef_search">
              <ElInput v-model.number="testForm.hnsw_ef_search" type="number" min="1" max="200" step="1" />
            </ElFormItem>
            <ElFormItem label="允许降级">
              <ElSelect v-model="testForm.allow_degraded_mode" class="full-width">
                <ElOption :value="true" label="允许" />
                <ElOption :value="false" label="禁止" />
              </ElSelect>
            </ElFormItem>
            <ElFormItem>
              <ElButton type="primary" :loading="testLoading" @click="runTest">执行测试</ElButton>
            </ElFormItem>
          </ElForm>
        </ElCard>
      </div>

      <div class="right-panel">
        <ElCard title="最近结果">
          <div v-if="currentResult" class="result-overview">
            <div class="kpi-grid">
              <div class="kpi-card">
                <span>recall@5</span>
                <strong>{{ metricText(currentResult.aggregated_metrics.recall_at_k, 4) }}</strong>
              </div>
              <div class="kpi-card">
                <span>ndcg@5</span>
                <strong>{{ metricText(currentResult.aggregated_metrics.ndcg_at_k, 4) }}</strong>
              </div>
              <div class="kpi-card">
                <span>mrr</span>
                <strong>{{ metricText(currentResult.aggregated_metrics.mrr_at_k, 4) }}</strong>
              </div>
              <div class="kpi-card">
                <span>avg_latency_ms</span>
                <strong>{{ metricText(currentResult.aggregated_metrics.avg_latency_ms, 2) }}</strong>
              </div>
            </div>

            <div v-if="currentHybridSummary" class="aux-grid">
              <div class="aux-card">
                <span>降级率</span>
                <strong>{{ metricText(currentHybridSummary.degraded_rate * 100, 1) }}%</strong>
              </div>
              <div class="aux-card">
                <span>向量命中率</span>
                <strong>{{ metricText(currentHybridSummary.vector_presence_rate * 100, 1) }}%</strong>
              </div>
            </div>

            <div class="chart-grid">
              <EChartPanel :option="currentQualityChart" height="280px" empty-text="当前结果暂无质量图" />
              <EChartPanel :option="currentLatencyChart" height="280px" empty-text="当前结果暂无延迟图" />
            </div>
          </div>
          <div v-else class="empty-block">
            先执行一次测试，再查看图表和指标。
          </div>
        </ElCard>

        <ElCard title="对比视图">
          <ElForm :model="compareForm" label-width="92px">
            <ElFormItem label="基线方案">
              <ElSelect v-model="compareForm.test_a" placeholder="选择基线方案" class="full-width">
                <ElOption v-for="item in historyOptions" :key="item.value" :label="item.label" :value="item.value" />
              </ElSelect>
            </ElFormItem>
            <ElFormItem label="优化方案">
              <ElSelect v-model="compareForm.test_b" placeholder="选择优化方案" class="full-width">
                <ElOption v-for="item in historyOptions" :key="item.value" :label="item.label" :value="item.value" />
              </ElSelect>
            </ElFormItem>
            <ElFormItem>
              <ElButton type="primary" :loading="compareLoading" @click="runComparison">执行对比</ElButton>
            </ElFormItem>
          </ElForm>

          <template v-if="compareResult">
            <ElDivider />
            <div class="result-overview compact">
              <div class="kpi-card">
                <span>基线方案</span>
                <strong>{{ formatRunShortId(compareResult.test_a.test_id) }}</strong>
              </div>
              <div class="kpi-card">
                <span>优化方案</span>
                <strong>{{ formatRunShortId(compareResult.test_b.test_id) }}</strong>
              </div>
              <div v-if="compareResult.diff.hybrid" class="kpi-card">
                <span>recall@5</span>
                <strong>{{ metricDeltaText(compareResult.diff.hybrid['recall@5'], 4) }}</strong>
              </div>
              <div v-if="compareResult.diff.hybrid" class="kpi-card">
                <span>avg_latency_ms</span>
                <strong>{{ metricDeltaText(compareResult.diff.hybrid.avg_latency_ms, 2) }}</strong>
              </div>
            </div>

            <div class="chart-grid">
              <EChartPanel :option="compareQualityChart" height="280px" empty-text="暂无对比质量图" />
              <EChartPanel :option="compareLatencyChart" height="280px" empty-text="暂无对比延迟图" />
            </div>
          </template>
        </ElCard>

        <ElCard title="历史趋势">
          <EChartPanel :option="historyTrendChart" height="320px" :loading="historyLoading" empty-text="暂无历史数据" />
        </ElCard>

        <ElCard title="历史记录" :loading="historyLoading">
          <ElTable :data="history" border>
            <ElTableColumn prop="test_id" label="测试编号" width="160" />
            <ElTableColumn prop="dataset_id" label="评测数据集" width="160" />
            <ElTableColumn label="版本" width="100">
              <template #default="scope">
                {{ scope.row.dataset_version }}
              </template>
            </ElTableColumn>
            <ElTableColumn label="recall@5" width="100">
              <template #default="scope">
                {{ metricText(scope.row['hybrid_recall@5'] * 100, 1) }}%
              </template>
            </ElTableColumn>
            <ElTableColumn label="ndcg@5" width="100">
              <template #default="scope">
                {{ metricText(scope.row['hybrid_ndcg@5'], 4) }}
              </template>
            </ElTableColumn>
            <ElTableColumn label="延迟" width="120">
              <template #default="scope">
                {{ metricText(scope.row.avg_latency_ms, 2) }}ms
              </template>
            </ElTableColumn>
            <ElTableColumn label="时间" width="180">
              <template #default="scope">
                {{ formatDate(scope.row.created_at) }}
              </template>
            </ElTableColumn>
            <ElTableColumn label="操作" width="220" fixed="right">
              <template #default="scope">
                <div class="action-row">
                  <ElButton link type="primary" @click="openDetail(scope.row.test_id)">查看详情</ElButton>
                  <ElButton link type="primary" @click="compareForm.test_a = scope.row.test_id">设为基线</ElButton>
                  <ElButton link type="danger" @click="handleDelete(scope.row.test_id)">删除</ElButton>
                </div>
              </template>
            </ElTableColumn>
          </ElTable>
        </ElCard>
      </div>
    </div>
  </div>
</template>

<style scoped>
.recall-test-page {
  max-width: 1440px;
  margin: 0 auto;
  padding: 20px;
}

.page-header {
  display: flex;
  align-items: center;
  gap: 16px;
  margin-bottom: 16px;
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
  background: #f1f5f9;
  border: none;
  border-radius: 10px;
  cursor: pointer;
}

.benchmark-banner {
  margin-bottom: 20px;
}

.banner-main {
  display: flex;
  justify-content: space-between;
  gap: 16px;
  align-items: flex-start;
}

.banner-kicker {
  color: #2563eb;
  font-size: 12px;
  font-weight: 700;
  letter-spacing: 0.08em;
  text-transform: uppercase;
}

.banner-main h2 {
  margin: 8px 0 6px;
}

.banner-main p {
  margin: 0;
  color: #64748b;
}

.banner-actions {
  display: flex;
  gap: 12px;
  margin-top: 16px;
}

.dataset-select,
.full-width {
  width: 100%;
}

.page-content {
  display: grid;
  grid-template-columns: minmax(360px, 1fr) minmax(520px, 1.35fr);
  gap: 20px;
  align-items: start;
}

.left-panel,
.right-panel {
  display: grid;
  gap: 20px;
}

.item-stack {
  display: grid;
  gap: 10px;
  width: 100%;
}

.inline-row {
  display: grid;
  grid-template-columns: 90px minmax(0, 1fr) 90px 28px;
  gap: 8px;
  align-items: center;
}

.short-input {
  width: 100%;
}

.grow-input {
  width: 100%;
}

.remove-btn {
  width: 28px;
  height: 28px;
  border: none;
  border-radius: 6px;
  background: #fee2e2;
  color: #dc2626;
  cursor: pointer;
}

.kpi-grid,
.aux-grid,
.chart-grid {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 12px;
}

.result-overview {
  display: grid;
  gap: 16px;
}

.result-overview.compact {
  grid-template-columns: repeat(2, minmax(0, 1fr));
}

.kpi-card,
.aux-card {
  padding: 14px;
  border-radius: 14px;
  background: linear-gradient(180deg, #f8fafc 0%, #ffffff 100%);
  border: 1px solid rgba(148, 163, 184, 0.18);
  display: grid;
  gap: 6px;
}

.kpi-card span,
.aux-card span {
  font-size: 12px;
  color: #64748b;
  text-transform: uppercase;
  letter-spacing: 0.04em;
}

.kpi-card strong,
.aux-card strong {
  font-size: 20px;
  color: #0f172a;
}

.empty-block {
  padding: 24px;
  color: #64748b;
  background: #f8fafc;
  border-radius: 14px;
  border: 1px dashed rgba(148, 163, 184, 0.35);
}

.action-row {
  display: flex;
  gap: 8px;
  flex-wrap: wrap;
}

@media (max-width: 1200px) {
  .page-content {
    grid-template-columns: 1fr;
  }

  .chart-grid,
  .kpi-grid,
  .aux-grid,
  .result-overview.compact {
    grid-template-columns: 1fr;
  }

  .inline-row {
    grid-template-columns: 1fr;
  }

  .banner-main,
  .banner-actions {
    flex-direction: column;
  }
}
</style>