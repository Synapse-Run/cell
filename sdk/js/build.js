const esbuild = require('esbuild');

esbuild.build({
  entryPoints: ['cell.ts'],
  bundle: true,
  outfile: 'dist/cell_browser.js',
  format: 'esm',
  target: 'es2020',
  platform: 'browser',
  // Map Node.js built-ins to the dummy implementation
  alias: {
    'fs': './dummy.js',
    'path': './dummy.js',
    'crypto': './dummy.js',
    'os': './dummy.js'
  },
  minify: true,
  sourcemap: true,
}).catch(() => process.exit(1));

console.log("Built dist/cell_browser.js successfully!");
