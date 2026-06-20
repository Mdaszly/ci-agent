import { createApp } from "vue";

import MainLayout from "./MainLayout.vue";
import router from "./router";
import ElementPlus from "element-plus";
import "element-plus/dist/index.css";
import "./styles.css";

createApp(MainLayout).use(ElementPlus).use(router).mount("#app");
