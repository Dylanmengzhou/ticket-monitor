#!/usr/bin/env node
import { spawn } from 'node:child_process';
import { readFile } from 'node:fs/promises';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { ensureRuntime, log } from './bootstrap.mjs';

const pkg = JSON.parse(await readFile(new URL('../package.json', import.meta.url), 'utf8'));
const args = process.argv.slice(2);

function commandName(argv) {
  for (let i = 0; i < argv.length; i++) {
    if (argv[i] === '--data-dir') { i++; continue; }
    if (argv[i].startsWith('--data-dir=')) continue;
    if (!argv[i].startsWith('-')) return argv[i];
  }
  return 'serve';
}

function printConfig() {
  // Use the actual node/CLI paths, also when launched from an npm cache.
  // This avoids npx.cmd shell handling and a second registry download at connection time.
  const data = args.findIndex(item => item === '--data-dir');
  const inline = args.find(item => item.startsWith('--data-dir='));
  const dataDir = data >= 0 ? args[data + 1] : inline?.slice('--data-dir='.length) || process.env.FLIGHT_MCP_DATA_DIR;
  if (data >= 0 && (!dataDir || dataDir.startsWith('--'))) throw new Error('--data-dir 后需要一个目录。');
  const extra = dataDir ? ['--data-dir', path.resolve(dataDir)] : [];
  const env = {};
  for (const key of ['FLIGHT_MCP_CACHE_DIR', 'PLAYWRIGHT_BROWSERS_PATH']) {
    if (process.env[key]) env[key] = key === 'PLAYWRIGHT_BROWSERS_PATH' && process.env[key] === '0'
      ? '0' : path.resolve(process.env[key]);
  }
  process.stdout.write(`${JSON.stringify({ mcpServers: { 'flight-agent': {
    command: process.execPath, args: [fileURLToPath(import.meta.url), ...extra, 'serve'],
    ...(Object.keys(env).length ? { env } : {}),
  } } }, null, 2)}\n`);
}

async function main() {
  if (args.includes('--version') || args.includes('-v')) { console.log(pkg.version); return; }
  if (args.includes('--help') || args.includes('-h')) {
    console.log(`Flight Agent MCP ${pkg.version}\n\n用法：flight-mcp [--data-dir 路径] [命令]\n\n  setup          自动安装 Python、查票依赖和浏览器，输出 MCP 配置\n  serve          运行 MCP stdio 服务（默认；缺少依赖时自动准备）\n  doctor         检查环境与携程登录状态\n  mcp-config     输出当前安装位置的 MCP 配置，不下载依赖\n  setup-browser  安装或修复查票浏览器\n  bark-configure 配置 iOS Bark 推送（私密输入推送地址）\n  bark-status    查看 Bark 配置，不显示 Key\n  bark-test      发送一条 Bark 测试通知\n  import-session 路径   从旧项目迁移本人的登录资料\n\n首次运行需要联网下载；只需预装 Node.js 22+。日志写 stderr。`);
    return;
  }
  const command = commandName(args);
  if (!['setup', 'serve', 'doctor', 'mcp-config', 'setup-browser', 'import-session',
        'bark-configure', 'bark-status', 'bark-test'].includes(command)) {
    throw new Error(`未知命令 ${command}，运行 flight-mcp --help 查看用法。`);
  }
  if (command === 'mcp-config') { printConfig(); return; }
  const { python, env } = await ensureRuntime({ browser: ['setup', 'setup-browser', 'serve'].includes(command) });
  if (command === 'setup') {
    log('安装完成。将下面的 JSON 添加到支持本地 stdio MCP 的 Agent 客户端，再让 Agent 配置各平台登录和 Bark。');
    printConfig();
    return;
  }
  // setup-browser was already handled above; no duplicate browser install.
  if (command === 'setup-browser') return;
  const child = spawn(python, ['-m', 'flight_mcp', ...args], { stdio: 'inherit', env, windowsHide: true });
  const handlers = new Map();
  for (const signal of ['SIGINT', 'SIGTERM']) {
    const forward = () => { if (!child.killed) child.kill(signal); };
    handlers.set(signal, forward);
    process.on(signal, forward);
  }
  try {
    await new Promise((resolve, reject) => {
      child.once('error', reject);
      child.once('exit', (code, signal) => {
        process.exitCode = code ?? (signal === 'SIGINT' ? 130 : 1);
        resolve();
      });
    });
  } finally {
    for (const [signal, handler] of handlers) process.off(signal, handler);
  }
}

await main().catch(error => {
  log(`启动失败：${error.message}`);
  process.exitCode = 1;
});
