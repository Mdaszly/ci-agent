<script setup lang="ts">
import { computed, onMounted, reactive, ref } from 'vue';
import {
  listMemoryItems,
  searchDecisionMemory,
  getMemoryStats,
  type DecisionMemoryItem,
  type DecisionChunkType,
  type MemoryStats,
} from '../services/api';

const stats = ref<MemoryStats | null>(null);
const items = ref<DecisionMemoryItem[]>([]);
const total = ref(0);
const loading = ref(false);
const errorMsg = ref('');

const searchQuery = ref('');
const searchResults = ref<DecisionMemoryItem[] | null>(null);
const searching = ref(false);

const filter = reactive({
  page: 1,
  pageSize: 20,
  chunkType: '' as DecisionChunkType | '',
  sortOrder: 'desc' as 'asc' | 'desc',
});

const expandedId = ref<string | null>(null);

const chunkTypeLabels: Record<string, string> = {
  decision: '决策',
  evidence: '证据',
  conflict: '冲突',
  repair: '修复',
  reviewer_feedback: '评审反馈',
  competitor_analysis: '竞品分析',
  market_intelligence: '市场情报',
  pricing_strategy: '定价策略',
};

const sortOrderLabels = {
  desc: '最新优先',
  asc: '最旧优先',
};

async function loadStats() {
  try {
    stats.value = await getMemoryStats();
  } catch {
    // 后端端点可能未实现
  }
}

async function loadItems() {
  loading.value = true;
  errorMsg.value = '';
  try {
    const result = await listMemoryItems(
      filter.page,
      filter.pageSize,
      filter.chunkType || undefined,
      filter.sortOrder,
    );
    items.value = result.items;
    total.value = result.total;
  } catch (err) {
    errorMsg.value = err instanceof Error ? err.message : '加载失败';
    items.value = [];
  } finally {
    loading.value = false;
  }
}

async function handleSearch() {
  if (!searchQuery.value.trim()) {
    searchResults.value = null;
    return;
  }
  searching.value = true;
  try {
    searchResults.value = await searchDecisionMemory(
      searchQuery.value.trim(),
      20,
      filter.sortOrder,
    );
  } catch (err) {
    errorMsg.value = err instanceof Error ? err.message : '搜索失败';
    searchResults.value = [];
  } finally {
    searching.value = false;
  }
}

function clearSearch() {
  searchQuery.value = '';
  searchResults.value = null;
}

function handleFilterChange() {
  filter.page = 1;
  loadItems();
}

function toggleExpand(id: string) {
  expandedId.value = expandedId.value === id ? null : id;
}

function formatTime(iso: string): string {
  return new Date(iso).toLocaleString('zh-CN', { hour12: false });
}

const displayList = computed(() => searchResults.value ?? items.value);

onMounted(() => {
  loadStats();
  loadItems();
});
</script>

<template>
  <div class="memory-explorer">
    <div class="info-panel">
      <div class="info-card">
        <span class="info-label">记忆浏览器用途</span>
        <p class="info-text">
          这里不是“展示页”，而是任务级记忆检索层：它把决策包、证据、冲突、修复和复核结果拆成可检索块，供后续同类任务复用、对照和回流修补。
        </p>
      </div>
      <div class="info-card">
        <span class="info-label">分块策略</span>
        <p class="info-text">
          一个决策包会拆成 decision、evidence、conflict、repair、reviewer_feedback 等块；每条证据和每次复核都会独立入库，支持按任务、类型、时间和语义命中。
        </p>
      </div>
    </div>

    <!-- 统计区 -->
    <div v-if="stats" class="stats-row">
      <div class="stat-cell">
        <span class="stat-value">{{ stats.total_items }}</span>
        <span class="stat-label">记忆块总数</span>
      </div>
      <div class="stat-cell">
        <span class="stat-value">{{ stats.total_checkpoints }}</span>
        <span class="stat-label">检查点总数</span>
      </div>
      <div class="stat-cell">
        <span class="stat-value">{{ stats.recent_checkpoints }}</span>
        <span class="stat-label">近期检查点</span>
      </div>
    </div>

    <div class="search-bar">
      <input
        v-model="searchQuery"
        type="text"
        class="search-input"
        placeholder="语义搜索决策记忆…支持竞品名、摘要、引用反查"
        @keyup.enter="handleSearch"
      />
      <button class="search-btn" :disabled="searching" @click="handleSearch">
        {{ searching ? '搜索中…' : '搜索' }}
      </button>
      <button v-if="searchResults" class="clear-btn" @click="clearSearch">
        清除
      </button>
    </div>

    <div class="filter-bar">
      <select v-model="filter.chunkType" class="filter-select" @change="handleFilterChange">
        <option value="">全部类型</option>
        <option v-for="(label, key) in chunkTypeLabels" :key="key" :value="key">
          {{ label }}
        </option>
      </select>
      <select v-model="filter.sortOrder" class="filter-select" @change="handleFilterChange">
        <option value="desc">{{ sortOrderLabels.desc }}</option>
        <option value="asc">{{ sortOrderLabels.asc }}</option>
      </select>
    </div>

    <!-- 错误提示 -->
    <p v-if="errorMsg" class="error-text">{{ errorMsg }}</p>

    <!-- 记忆块列表 -->
    <div class="item-list">
      <div
        v-for="item in displayList"
        :key="item.id"
        class="item-card"
        :class="{ expanded: expandedId === item.id }"
        @click="toggleExpand(item.id)"
      >
        <div class="item-header">
          <span class="type-tag" :data-type="item.chunk_type">
            {{ chunkTypeLabels[item.chunk_type] || item.chunk_type }}
          </span>
          <span class="item-summary">{{ item.summary }}</span>
        </div>
        <div class="item-meta">
          <span>{{ item.task_id }}</span>
          <span>v{{ item.version }}</span>
          <span>迭代 {{ item.iteration }}</span>
          <span :data-status="item.status">{{ statusLabels[item.status] || item.status }}</span>
          <span>{{ formatTime(item.created_at) }}</span>
        </div>
        <div v-if="expandedId === item.id" class="item-detail">
          <div v-if="item.source_refs.length" class="detail-section">
            <span class="detail-label">来源引用</span>
            <div class="ref-list">
              <code v-for="ref in item.source_refs" :key="ref">{{ ref }}</code>
            </div>
          </div>
          <div v-if="Object.keys(item.payload).length" class="detail-section">
            <span class="detail-label">负载数据</span>
            <pre class="payload-json">{{ JSON.stringify(item.payload, null, 2) }}</pre>
          </div>
        </div>
      </div>

      <div v-if="!loading && displayList.length === 0" class="empty-state">
        <p>暂无记忆块{{ searchResults ? '匹配搜索结果' : '' }}</p>
      </div>
    </div>

    <!-- 分页 -->
    <div v-if="!searchResults && total > filter.pageSize" class="pagination">
      <button
        class="page-btn"
        :disabled="filter.page <= 1"
        @click="filter.page--; loadItems()"
      >
        上一页
      </button>
      <span class="page-info">第 {{ filter.page }} 页 / 共 {{ Math.ceil(total / filter.pageSize) }} 页</span>
      <button
        class="page-btn"
        :disabled="filter.page >= Math.ceil(total / filter.pageSize)"
        @click="filter.page++; loadItems()"
      >
        下一页
      </button>
    </div>
  </div>
</template>

<style scoped>
.memory-explorer {
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

.stats-row {
  display: flex;
  gap: 16px;
  margin-bottom: 24px;
}

.stat-cell {
  flex: 1;
  background: rgba(255, 255, 255, 0.8);
  border: 1px solid rgba(148, 163, 184, 0.2);
  border-radius: 10px;
  padding: 16px 20px;
  display: flex;
  flex-direction: column;
  gap: 4px;
}

.stat-value {
  font-size: 28px;
  font-weight: 700;
  color: #2563eb;
}

.stat-label {
  font-size: 12px;
  color: #64748b;
}

.search-bar {
  display: flex;
  gap: 8px;
  margin-bottom: 16px;
}

.search-input {
  flex: 1;
  padding: 10px 14px;
  border: 1px solid rgba(148, 163, 184, 0.4);
  border-radius: 8px;
  font-size: 14px;
  background: #fff;
}

.search-input:focus {
  outline: none;
  border-color: #2563eb;
  box-shadow: 0 0 0 3px rgba(37, 99, 235, 0.12);
}

.search-btn {
  padding: 10px 20px;
  border: none;
  border-radius: 8px;
  background: #2563eb;
  color: #fff;
  font-size: 14px;
  font-weight: 600;
  cursor: pointer;
}

.search-btn:disabled {
  opacity: 0.6;
}

.clear-btn {
  padding: 10px 16px;
  border: 1px solid rgba(148, 163, 184, 0.4);
  border-radius: 8px;
  background: #fff;
  color: #475569;
  font-size: 14px;
  cursor: pointer;
}

.filter-bar {
  margin-bottom: 16px;
}

.filter-select {
  padding: 8px 12px;
  border: 1px solid rgba(148, 163, 184, 0.4);
  border-radius: 8px;
  font-size: 14px;
  background: #fff;
  color: #0f172a;
}

.error-text {
  color: #dc2626;
  font-size: 13px;
  margin: 0 0 16px;
}

.item-list {
  display: flex;
  flex-direction: column;
  gap: 10px;
}

.item-card {
  background: rgba(255, 255, 255, 0.9);
  border: 1px solid rgba(148, 163, 184, 0.2);
  border-radius: 10px;
  padding: 14px 18px;
  cursor: pointer;
  transition: border-color 0.18s ease;
}

.item-card:hover {
  border-color: rgba(37, 99, 235, 0.3);
}

.item-card.expanded {
  border-color: rgba(37, 99, 235, 0.4);
}

.item-header {
  display: flex;
  align-items: center;
  gap: 10px;
  margin-bottom: 6px;
}

.type-tag {
  flex-shrink: 0;
  padding: 2px 8px;
  border-radius: 4px;
  font-size: 11px;
  font-weight: 600;
  background: #eff6ff;
  color: #2563eb;
}

.type-tag[data-type="evidence"] { background: #f0fdf4; color: #16a34a; }
.type-tag[data-type="conflict"] { background: #fef2f2; color: #dc2626; }
.type-tag[data-type="repair"] { background: #fffbeb; color: #d97706; }
.type-tag[data-type="reviewer_feedback"] { background: #faf5ff; color: #9333ea; }

.item-summary {
  font-size: 14px;
  color: #0f172a;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.item-meta {
  display: flex;
  gap: 12px;
  font-size: 12px;
  color: #94a3b8;
}

.item-meta [data-status="approved"] { color: #16a34a; }
.item-meta [data-status="rejected"] { color: #dc2626; }
.item-meta [data-status="superseded"] { color: #94a3b8; }

.item-detail {
  margin-top: 12px;
  padding-top: 12px;
  border-top: 1px solid rgba(148, 163, 184, 0.15);
}

.detail-section {
  margin-bottom: 12px;
}

.detail-label {
  display: block;
  font-size: 12px;
  font-weight: 600;
  color: #475569;
  margin-bottom: 6px;
}

.ref-list {
  display: flex;
  flex-wrap: wrap;
  gap: 6px;
}

.ref-list code {
  font-size: 11px;
  padding: 2px 6px;
  background: #f1f5f9;
  border-radius: 4px;
  color: #475569;
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

.pagination {
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 16px;
  margin-top: 24px;
}

.page-btn {
  padding: 8px 16px;
  border: 1px solid rgba(148, 163, 184, 0.4);
  border-radius: 8px;
  background: #fff;
  color: #2563eb;
  font-size: 13px;
  cursor: pointer;
}

.page-btn:disabled {
  opacity: 0.4;
  cursor: not-allowed;
}

.page-info {
  font-size: 13px;
  color: #64748b;
}
</style>
