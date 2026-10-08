import basicSsl from "@vitejs/plugin-basic-ssl";
import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

// In development the UI calls /api and /data on the same origin; Vite forwards them to FastAPI.
// The dev server is HTTPS (self-signed) so phones on the LAN get camera, microphone and speech
// APIs, which browsers only expose on a secure origin. The proxy to FastAPI stays plain HTTP.
export default defineConfig({
  plugins: [react(), basicSsl()],
  server: {
    https: {},
    proxy: { "/api": "http://127.0.0.1:8000", "/data": "http://127.0.0.1:8000" },
  },
});
