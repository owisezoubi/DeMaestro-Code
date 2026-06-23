import { defineConfig, loadEnv } from "vite"
import react from "@vitejs/plugin-react"
import path from "path"

export default defineConfig(({ mode }) => {
  // loadEnv with "" prefix includes all env vars (not just VITE_*)
  const env = loadEnv(mode, process.cwd(), "")
  // Shell-exported BACKEND_PORT (set by start.sh after port conflict detection)
  // takes precedence over .env file values
  const backendPort = process.env.BACKEND_PORT || env.BACKEND_PORT || "8100"
  const vitePort = parseInt(process.env.VITE_PORT || env.VITE_PORT || "5273", 10)

  return {
    plugins: [react()],
    resolve: {
      alias: { "@": path.resolve(__dirname, "./src") },
    },
    server: {
      port: vitePort,
      strictPort: false,
      host: true,
      proxy: {
        // Dev: proxy all /api/* calls to the backend so CORS and port
        // mismatches are eliminated entirely.
        "/api": {
          target: `http://localhost:${backendPort}`,
          changeOrigin: true,
        },
      },
    },
    preview: { port: vitePort, strictPort: false },
  }
})
