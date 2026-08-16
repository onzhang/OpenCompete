import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'
import { fileURLToPath, URL } from 'node:url'

export default defineConfig({
  plugins: [vue()],
  resolve: {
    alias: {
      '@': fileURLToPath(new URL('./src', import.meta.url)),
    },
  },
  server: {
    port: 5178,
    proxy: {
      // 转发到本地 opencompete-api 服务（uv run opencompete-api --port 8123）
      '/api': {
        target: 'http://127.0.0.1:8123',
        changeOrigin: true,
      },
    },
  },
})
