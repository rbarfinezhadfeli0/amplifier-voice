import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import fs from 'fs'

// https://vite.dev/config/
export default defineConfig({
  plugins: [react()],
  server: {
    allowedHosts: ['spark-1'],
    https: fs.existsSync('/tmp/spark-1-cert.pem') ? {
      key: fs.readFileSync('/tmp/spark-1-key.pem'),
      cert: fs.readFileSync('/tmp/spark-1-cert.pem'),
    } : undefined,
  },
})
