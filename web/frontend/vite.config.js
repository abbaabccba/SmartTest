import { defineConfig } from 'vite'
import { resolve } from 'node:path'
import { WEB_BRAND_NAME, WEB_BRAND_TOKEN } from './src/brand.js'
import { initializeTheme } from './src/theme.js'

export default defineConfig({
  plugins: [{
    name: 'smarttest-product-motion',
    transformIndexHtml: { order: 'pre', handler: (_html, { filename }) => filename === resolve(import.meta.dirname, 'login.html') ? [] : [{
      tag: 'script', attrs: { type: 'module' }, children: "import { initializeProductLineMotion } from '/src/product-line-motion.js'; initializeProductLineMotion();", injectTo: 'body',
    }] },
  }, {
    name: 'smarttest-web-brand',
    transformIndexHtml: html => html.replaceAll(WEB_BRAND_TOKEN, WEB_BRAND_NAME)
  }, {
    name: 'smarttest-theme-first-paint',
    transformIndexHtml: { order: 'pre', handler: () => [{
      tag: 'script', children: `(${initializeTheme.toString()})()`, injectTo: 'head-pre',
    }] },
  }],
  build: {
    rollupOptions: {
      input: {
        dashboard: resolve(import.meta.dirname, 'index.html'),
        projects: resolve(import.meta.dirname, 'projects.html'),
        testManagement: resolve(import.meta.dirname, 'test-management.html'),
        inbox: resolve(import.meta.dirname, 'inbox.html'),
        analytics: resolve(import.meta.dirname, 'analytics.html'),
        settings: resolve(import.meta.dirname, 'settings.html'),
        auditEmail: resolve(import.meta.dirname, 'audit-email.html'),
        jira: resolve(import.meta.dirname, 'jira.html'),
        tools: resolve(import.meta.dirname, 'tools.html'),
        login: resolve(import.meta.dirname, 'login.html'),
        loginMotionPreview: resolve(import.meta.dirname, 'login-motion-preview.html')
      }
    }
  },
  server: {
    proxy: {
      '/api': {
        target: 'http://127.0.0.1:8000',
        changeOrigin: true
      }
    }
  }
})
