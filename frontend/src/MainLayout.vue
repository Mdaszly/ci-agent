<script setup lang="ts">
import { onMounted, ref } from 'vue';
import { useRouter } from 'vue-router';
import { getCurrentUser, logout, type UserInfo } from './services/api';

const router = useRouter();
const user = ref<UserInfo | null>(null);

const navItems = [
  { label: '首页', path: '/' },
  { label: '召回测试', path: '/recall' },
  { label: 'Bad Case', path: '/bad-cases' },
  { label: '记忆浏览器', path: '/memory' },
  { label: '上下文监控', path: '/context' },
  { label: '检查点', path: '/checkpoints' },
  { label: 'Agent 增强', path: '/agent-enhancements' },
];

onMounted(async () => {
  try {
    user.value = await getCurrentUser();
  } catch {
    // token 无效时路由守卫会处理
  }
});

function handleLogout() {
  logout();
  router.push({ name: 'Login' });
}
</script>

<template>
  <div class="main-layout">
    <header class="topbar">
      <div class="brand-block">
        <div class="brand-kicker">Evidence-First Agent</div>
        <div class="brand-title">竞品情报决策工作台</div>
      </div>

      <nav class="nav-group">
        <button
          v-for="item in navItems"
          :key="item.path"
          type="button"
          class="nav-btn"
          @click="router.push(item.path)"
        >
          {{ item.label }}
        </button>
      </nav>

      <div class="user-block">
        <span v-if="user" class="user-name">{{ user.username }}</span>
        <button type="button" class="logout-btn" @click="handleLogout">
          登出
        </button>
      </div>
    </header>

    <main class="main-content">
      <router-view />
    </main>
  </div>
</template>

<style scoped>
.main-layout {
  min-height: 100vh;
  background:
    radial-gradient(circle at top left, rgba(37, 99, 235, 0.08), transparent 28%),
    linear-gradient(180deg, #f8fafc 0%, #eef4ff 100%);
}

.topbar {
  position: sticky;
  top: 0;
  z-index: 10;
  display: flex;
  justify-content: space-between;
  gap: 16px;
  align-items: center;
  padding: 16px 24px;
  background: rgba(255, 255, 255, 0.82);
  backdrop-filter: blur(12px);
  border-bottom: 1px solid rgba(148, 163, 184, 0.2);
}

.brand-block {
  display: grid;
  gap: 4px;
}

.brand-kicker {
  color: #2563eb;
  font-size: 12px;
  font-weight: 700;
  letter-spacing: 0.08em;
  text-transform: uppercase;
}

.brand-title {
  font-size: 18px;
  font-weight: 700;
  color: #0f172a;
}

.nav-group {
  display: flex;
  gap: 10px;
  flex-wrap: wrap;
}

.nav-btn {
  padding: 9px 14px;
  border: 1px solid rgba(148, 163, 184, 0.35);
  border-radius: 999px;
  background: #fff;
  color: #0f172a;
  cursor: pointer;
  transition: transform 0.18s ease, box-shadow 0.18s ease, border-color 0.18s ease;
}

.nav-btn:hover {
  transform: translateY(-1px);
  border-color: rgba(37, 99, 235, 0.45);
  box-shadow: 0 8px 24px rgba(15, 23, 42, 0.08);
}

.user-block {
  display: flex;
  align-items: center;
  gap: 10px;
}

.user-name {
  font-size: 13px;
  font-weight: 600;
  color: #475569;
}

.logout-btn {
  padding: 7px 12px;
  border: 1px solid rgba(148, 163, 184, 0.35);
  border-radius: 999px;
  background: #fff;
  color: #475569;
  font-size: 13px;
  cursor: pointer;
  transition: border-color 0.18s ease, color 0.18s ease;
}

.logout-btn:hover {
  border-color: rgba(220, 38, 38, 0.4);
  color: #dc2626;
}

.main-content {
  padding: 24px;
}

@media (max-width: 760px) {
  .topbar {
    align-items: flex-start;
    flex-direction: column;
  }

  .main-content {
    padding: 16px;
  }
}
</style>