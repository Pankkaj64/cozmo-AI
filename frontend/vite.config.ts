import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

// In development the UI calls /api and /data on the same origin; Vite forwards them to FastAPI.
export default defineConfig({
  plugins: [react()],
  server: { proxy: { "/api": "http://127.0.0.1:8000", "/data": "http://127.0.0.1:8000" } },
});
