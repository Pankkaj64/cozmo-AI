import { defineConfig, type Plugin } from 'vite';
import react from '@vitejs/plugin-react';

function browserTerminalLogs(): Plugin {
  return {
    name: 'browser-terminal-logs',
    apply: 'serve',
    configureServer(server) {
      server.ws.on('app:log', (entry) => {
        if (!entry || typeof entry.step !== 'string') return;
        const level = ['info', 'warn', 'error'].includes(entry.level) ? entry.level : 'info';
        // Escape control characters and bound each entry before writing to a terminal.
        const line = `[frontend] ${level.toUpperCase()} ${JSON.stringify(entry).slice(0, 6000)}`;
        if (level === 'error') server.config.logger.error(line);
        else if (level === 'warn') server.config.logger.warn(line);
        else server.config.logger.info(line);
      });
      server.config.logger.info('[frontend] Browser step logs enabled; waiting for the page to connect.');
    },
  };
}

export default defineConfig({
  plugins: [react(), browserTerminalLogs()],
  server: { proxy: { '/api': 'http://127.0.0.1:8000', '/data': 'http://127.0.0.1:8000' } },
});
