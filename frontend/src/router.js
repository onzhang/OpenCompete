import { createRouter, createWebHashHistory } from 'vue-router'

const DashboardPage = () => import('./pages/DashboardPage.vue')
const NewResearchPage = () => import('./pages/NewResearchPage.vue')
const ArchivePage = () => import('./pages/ArchivePage.vue')
const RunDetailPage = () => import('./pages/RunDetailPage.vue')
const ReportPage = () => import('./pages/ReportPage.vue')

export const router = createRouter({
  history: createWebHashHistory(),
  routes: [
    { path: '/', name: 'dashboard', component: DashboardPage },
    { path: '/new', name: 'new-research', component: NewResearchPage },
    { path: '/runs', name: 'archive', component: ArchivePage },
    { path: '/runs/:id', name: 'run-detail', component: RunDetailPage },
    { path: '/runs/:id/report', name: 'report', component: ReportPage },
  ],
  scrollBehavior() {
    return { top: 0 }
  },
})
