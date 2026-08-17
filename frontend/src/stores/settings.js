import { defineStore } from 'pinia'
import { api } from '../api/client.js'
import { useRunsStore } from './runs.js'

export const useSettingsStore = defineStore('settings', {
  state: () => ({
    view: null,
    loading: false,
    saving: false,
    error: null,
  }),

  actions: {
    async fetch() {
      this.loading = true
      this.error = null
      try {
        this.view = await api.settings()
        return this.view
      } catch (err) {
        this.error = err.message
        throw err
      } finally {
        this.loading = false
      }
    },

    async save(values) {
      if (!this.view) await this.fetch()
      this.saving = true
      this.error = null
      try {
        this.view = await api.updateSettings({
          expected_revision: this.view.revision,
          values,
        })
        const runs = useRunsStore()
        runs.budgetLimit = null
        await runs.fetchConfig()
        return this.view
      } catch (err) {
        this.error = err.message
        throw err
      } finally {
        this.saving = false
      }
    },

    async changeCredential({ name, operation, value = null }) {
      if (!this.view) await this.fetch()
      this.error = null
      try {
        this.view = await api.updateCredential({
          expected_revision: this.view.revision,
          name,
          operation,
          value,
        })
        return this.view
      } catch (err) {
        this.error = err.message
        throw err
      }
    },

    async testConnection(target) {
      return api.testConnection(target)
    },
  },
})
