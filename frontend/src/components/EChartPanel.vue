<script setup lang="ts">
import * as echarts from 'echarts';
import { nextTick, onBeforeUnmount, onMounted, ref, shallowRef, watch } from 'vue';
import type { ECharts, EChartsOption } from 'echarts';

const props = withDefaults(
  defineProps<{
    option?: EChartsOption | null;
    height?: string;
    loading?: boolean;
    emptyText?: string;
  }>(),
  {
    height: '320px',
    loading: false,
    emptyText: '暂无数据',
  },
);

const chartRef = ref<HTMLDivElement | null>(null);
const chart = shallowRef<ECharts | null>(null);
let resizeObserver: ResizeObserver | null = null;

function resizeChart() {
  chart.value?.resize();
}

function renderChart() {
  if (!chart.value || !props.option) return;
  chart.value.setOption(props.option, true);
  chart.value.hideLoading();
}

function mountChart() {
  if (!chartRef.value) return;

  chart.value = echarts.init(chartRef.value);
  if (props.loading) {
    chart.value.showLoading('default', {
      text: '加载中...',
    });
  }

  renderChart();

  resizeObserver = new ResizeObserver(() => {
    resizeChart();
  });
  resizeObserver.observe(chartRef.value);
}

watch(
  () => props.option,
  async () => {
    await nextTick();
    renderChart();
  },
  { deep: true },
);

watch(
  () => props.loading,
  (loading) => {
    if (!chart.value) return;
    if (loading) {
      chart.value.showLoading('default', { text: '加载中...' });
      return;
    }
    chart.value.hideLoading();
  },
  { immediate: true },
);

onMounted(async () => {
  await nextTick();
  mountChart();
});

onBeforeUnmount(() => {
  resizeObserver?.disconnect();
  resizeObserver = null;
  chart.value?.dispose();
  chart.value = null;
});
</script>

<template>
  <div class="chart-panel" :style="{ height }">
    <div v-if="!option && !loading" class="chart-empty">
      {{ emptyText }}
    </div>
    <div ref="chartRef" class="chart-canvas"></div>
  </div>
</template>

<style scoped>
.chart-panel {
  position: relative;
  width: 100%;
  min-height: 220px;
  border-radius: 16px;
  overflow: hidden;
  background: linear-gradient(180deg, rgba(248, 250, 252, 0.9), rgba(255, 255, 255, 0.95));
  border: 1px solid rgba(148, 163, 184, 0.18);
}

.chart-canvas {
  width: 100%;
  height: 100%;
  min-height: inherit;
}

.chart-empty {
  position: absolute;
  inset: 0;
  display: grid;
  place-items: center;
  color: #64748b;
  font-size: 14px;
  z-index: 1;
  pointer-events: none;
}
</style>