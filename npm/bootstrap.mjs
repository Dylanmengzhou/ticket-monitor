import { createHash, randomUUID } from 'node:crypto';
import { spawn } from 'node:child_process';
import { access, chmod, mkdir, readFile, rename, rm, stat, utimes, writeFile } from 'node:fs/promises';
import { homedir } from 'node:os';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { gunzipSync } from 'node:zlib';
import { unzipSync } from 'fflate';

export const packageRoot = fileURLToPath(new URL('../', import.meta.url));
const assets = JSON.parse(await readFile(new URL('./uv-assets.json', import.meta.url), 'utf8'));
export const log = message => process.stderr.write(`[flight-mcp] ${message}\n`);
const pause = ms => new Promise(resolve => setTimeout(resolve, ms));

export function cacheDirectory(env = process.env, platform = process.platform, home = homedir()) {
  if (env.FLIGHT_MCP_CACHE_DIR) return path.resolve(env.FLIGHT_MCP_CACHE_DIR);
  const base = platform === 'win32' ? (env.LOCALAPPDATA || path.join(home, 'AppData', 'Local'))
    : platform === 'darwin' ? path.join(home, 'Library', 'Caches')
      : (env.XDG_CACHE_HOME || path.join(home, '.cache'));
  return path.join(base, 'flight-agent-mcp');
}

export function platformAsset(platform = process.platform, arch = process.arch) {
  const asset = assets.assets[`${platform}-${arch}`];
  if (!asset) throw new Error(`暂不支持 ${platform}/${arch}，请使用 64 位 macOS、Windows 或 Linux。`);
  return { name: asset[0], sha256: asset[1], version: assets.version };
}

export function verifyDownload(buffer, expected) {
  if (createHash('sha256').update(buffer).digest('hex') !== expected) {
    throw new Error('uv 下载文件校验失败，请重试。');
  }
}

// Extract only the expected executable into memory; never write archive paths to disk.
export function extractExecutable(buffer, archiveName) {
  if (archiveName.endsWith('.zip')) {
    const files = unzipSync(buffer, { filter: item => item.name === 'uv.exe' && item.originalSize < 128 * 1024 * 1024 });
    if (!files['uv.exe']?.length) throw new Error('uv ZIP 中没有找到 uv.exe');
    return Buffer.from(files['uv.exe']);
  }
  const tar = gunzipSync(buffer, { maxOutputLength: 150 * 1024 * 1024 });
  for (let offset = 0; offset + 512 <= tar.length;) {
    const header = tar.subarray(offset, offset + 512);
    const name = header.subarray(0, 100).toString('utf8').split('\0')[0];
    if (!name) break;
    const size = Number.parseInt(header.subarray(124, 136).toString('ascii').replace(/\0/g, '').trim(), 8);
    if (!Number.isSafeInteger(size) || size < 0 || offset + 512 + size > tar.length) {
      throw new Error('uv TAR 文件格式无效');
    }
    if (name === 'uv' || name === `${archiveName.replace(/\.tar\.gz$/, '')}/uv`) {
      if (![0, 48].includes(header[156]) || !size) throw new Error('uv 可执行文件格式无效');
      return tar.subarray(offset + 512, offset + 512 + size);
    }
    offset += 512 + Math.ceil(size / 512) * 512;
  }
  throw new Error('uv TAR 中没有找到可执行文件');
}

export async function download(url) {
  const response = await fetch(url, { signal: AbortSignal.timeout(180_000) });
  if (!response.ok) throw new Error(`下载失败 (${response.status}): ${url}`);
  const data = Buffer.from(await response.arrayBuffer());
  if (data.length > 64 * 1024 * 1024) throw new Error('uv 下载文件过大');
  return data;
}

async function exists(filename) {
  try { await access(filename); return true; } catch { return false; }
}

export async function withLock(filename, action, timeoutMs = 900_000) {
  const deadline = Date.now() + timeoutMs;
  await mkdir(path.dirname(filename), { recursive: true, mode: 0o700 });
  while (true) {
    try { await mkdir(filename, { mode: 0o700 }); break; }
    catch (error) {
      if (error.code !== 'EEXIST') throw error;
      // A crashed bootstrap cannot keep future installs locked indefinitely.
      const info = await stat(filename).catch(() => null);
      if (info && Date.now() - info.mtimeMs > 120_000) {
        await rm(filename, { recursive: true, force: true });
        continue;
      }
      if (Date.now() >= deadline) throw new Error('等待首次安装超时，请稍后重试。');
      await pause(300);
    }
  }
  const heartbeat = setInterval(() => utimes(filename, new Date(), new Date()).catch(() => {}), 10_000);
  try { return await action(); }
  finally { clearInterval(heartbeat); await rm(filename, { recursive: true, force: true }); }
}

export function run(command, args, options = {}) {
  return new Promise((resolve, reject) => {
    const child = spawn(command, args, {
      stdio: ['ignore', 2, 2], windowsHide: true, ...options,
    });
    child.once('error', reject);
    child.once('exit', (code, signal) => code === 0 ? resolve()
      : reject(new Error(`${path.basename(command)} 退出 (${code ?? signal})`)));
  });
}

async function ensureUv(cache) {
  const asset = platformAsset();
  const folder = path.join(cache, `uv-${asset.version}-${process.platform}-${process.arch}`);
  const executable = path.join(folder, process.platform === 'win32' ? 'uv.exe' : 'uv');
  if (await exists(executable)) return executable;
  await mkdir(folder, { recursive: true, mode: 0o700 });
  log('首次运行：下载 Python 环境管理器…');
  const base = process.env.FLIGHT_MCP_UV_DOWNLOAD_BASE_URL || assets.baseUrl;
  const buffer = await download(`${base.replace(/\/$/, '')}/${asset.version}/${asset.name}`);
  verifyDownload(buffer, asset.sha256);
  const temporary = `${executable}.${randomUUID()}.tmp`;
  await writeFile(temporary, extractExecutable(buffer, asset.name), { mode: 0o700 });
  await chmod(temporary, 0o700);
  await rename(temporary, executable);
  return executable;
}

export async function ensureRuntime({ browser = false } = {}) {
  platformAsset();
  const payload = path.join(packageRoot, 'npm', 'payload');
  const manifest = JSON.parse(await readFile(path.join(payload, 'manifest.json'), 'utf8'));
  const wheel = path.join(payload, manifest.wheel);
  const lock = path.join(payload, 'requirements.txt');
  verifyDownload(await readFile(wheel), manifest.sha256);
  verifyDownload(await readFile(lock), manifest.requirementsSha256);
  const cache = cacheDirectory();
  const root = path.join(cache, `runtime-${manifest.sha256.slice(0, 16)}-${manifest.requirementsSha256.slice(0, 12)}`);
  const venv = path.join(root, 'venv');
  const python = path.join(venv, process.platform === 'win32' ? 'Scripts/python.exe' : 'bin/python');
  const ready = path.join(root, 'ready.json');
  const env = { ...process.env, UV_PYTHON_INSTALL_DIR: path.join(cache, 'python'),
    UV_CACHE_DIR: path.join(cache, 'uv-cache'), UV_NO_PROGRESS: '1', PYTHONUTF8: '1' };
  await withLock(path.join(cache, 'bootstrap.lock'), async () => {
    if (!(await exists(ready)) || !(await exists(python))) {
      const uv = await ensureUv(cache);
      await mkdir(root, { recursive: true, mode: 0o700 });
      await rm(venv, { recursive: true, force: true });
      log('首次运行：准备独立 Python 3.12 和查票依赖…');
      await run(uv, ['--no-config', 'venv', '--python', '3.12', '--managed-python', venv], { env });
      await run(uv, ['--no-config', 'pip', 'install', '--python', python, '--only-binary', ':all:',
        '--constraint', lock, wheel], { env });
      await writeFile(ready, JSON.stringify({ version: manifest.version, python }), { mode: 0o600 });
    }
    if (browser) {
      log('检查查票浏览器，首次使用会自动下载…');
      await run(python, ['-m', 'playwright', 'install', 'chromium'], { env });
    }
  });
  return { python, env };
}
