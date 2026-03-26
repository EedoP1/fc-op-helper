import { defineConfig } from 'wxt';

export default defineConfig({
  manifest: {
    name: 'OP Seller',
    description: 'FC26 OP sell automation assistant',
    version: '0.1.0',
    permissions: ['alarms', 'storage', 'tabs'],
    host_permissions: [
      'http://localhost:8000/*',
      'https://www.ea.com/*',
    ],
  },
});
