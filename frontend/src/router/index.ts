import { createRouter, createWebHistory } from "vue-router";

import AgentEnhancements from "../views/AgentEnhancements.vue";
import App from "../App.vue";
import BadCaseManagement from "../views/BadCaseManagement.vue";
import CheckpointTimeline from "../views/CheckpointTimeline.vue";
import ContextMonitor from "../views/ContextMonitor.vue";
import Login from "../views/Login.vue";
import MemoryExplorer from "../views/MemoryExplorer.vue";
import RecallTest from "../views/RecallTest.vue";
import RecallTestDetail from "../views/RecallTestDetail.vue";
import TaskDetail from "../views/TaskDetail.vue";
import { isAuthenticated } from "../services/api";

const routes = [
  {
    path: "/login",
    name: "Login",
    component: Login,
    meta: { public: true },
  },
  {
    path: "/",
    name: "Dashboard",
    component: App,
  },
  {
    path: "/tasks/:id",
    name: "TaskDetail",
    component: TaskDetail,
  },
  {
    path: "/recall",
    name: "RecallTest",
    component: RecallTest,
  },
  {
    path: "/recall/:testId",
    name: "RecallTestDetail",
    component: RecallTestDetail,
  },
  {
    path: "/bad-cases",
    name: "BadCaseManagement",
    component: BadCaseManagement,
  },
  {
    path: "/memory",
    name: "MemoryExplorer",
    component: MemoryExplorer,
  },
  {
    path: "/context",
    name: "ContextMonitor",
    component: ContextMonitor,
  },
  {
    path: "/checkpoints",
    name: "CheckpointTimeline",
    component: CheckpointTimeline,
  },
  {
    path: "/agent-enhancements",
    name: "AgentEnhancements",
    component: AgentEnhancements,
  },
];

const router = createRouter({
  history: createWebHistory(),
  routes,
});

router.beforeEach((to) => {
  if (to.meta.public) {
    return true;
  }
  if (!isAuthenticated()) {
    return { name: "Login" };
  }
  return true;
});

export default router;
