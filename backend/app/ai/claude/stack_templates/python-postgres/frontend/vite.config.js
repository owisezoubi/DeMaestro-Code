import { defineConfig, loadEnv } from "vite";
import react from "@vitejs/plugin-react";
import path from "path";

export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, process.cwd(), "VITE_");
  const port = Number(env.VITE_PORT) || 5273;
  return {
    plugins: [react()],
    server: { port, strictPort: false, host: true },
    preview: { port, strictPort: false },
    resolve: { alias: { "@": path.resolve(__dirname, "./src") } },
  };
});
