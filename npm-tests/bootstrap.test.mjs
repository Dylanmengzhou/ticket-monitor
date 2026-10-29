import assert from 'node:assert/strict';
import { createHash } from 'node:crypto';
import { spawnSync } from 'node:child_process';
import { mkdtemp, rm, symlink } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import path from 'node:path';
import { test } from 'node:test';
import { gzipSync } from 'node:zlib';
import { zipSync } from 'fflate';
import { cacheDirectory, extractExecutable, packageRoot, platformAsset, verifyDownload, withLock } from '../npm/bootstrap.mjs';

test('selects Mac and Windows binaries, refuses unknown architectures', () => {
  assert.match(platformAsset('darwin', 'arm64').name, /aarch64-apple/);
  assert.match(platformAsset('darwin', 'x64').name, /x86_64-apple/);
  assert.match(platformAsset('win32', 'x64').name, /windows-msvc.zip$/);
  assert.match(platformAsset('win32', 'arm64').name, /aarch64-pc-windows/);
  assert.throws(() => platformAsset('win32', 'ia32'), /暂不支持/);
  assert.equal(cacheDirectory({ FLIGHT_MCP_CACHE_DIR: '/tmp/custom cache' }), path.resolve('/tmp/custom cache'));
  assert.equal(cacheDirectory({ LOCALAPPDATA: 'local' }, 'win32', 'home'), path.join('local', 'flight-agent-mcp'));
});

test('checks archive integrity before extraction', () => {
  const original = Buffer.from('verified executable');
  const digest = createHash('sha256').update(original).digest('hex');
  verifyDownload(original, digest);
  assert.throws(() => verifyDownload(Buffer.from('modified'), digest), /校验失败/);
});

test('extracts the expected Windows executable, ignores other ZIP entries', () => {
  const zip = Buffer.from(zipSync({ 'uv.exe': Buffer.from('MZ-test'), '../ignore.exe': Buffer.from('other') }));
  assert.equal(extractExecutable(zip, 'uv-win.zip').toString(), 'MZ-test');
  assert.throws(() => extractExecutable(Buffer.from(zipSync({ 'elsewhere/uv.exe': Buffer.from('bad') })), 'uv.zip'), /没有找到/);
});

function tarEntry(name, data, type = 48) {
  const header = Buffer.alloc(512);
  header.write(name);
  header.write(`${data.length.toString(8).padStart(11, '0')}\0`, 124, 'ascii');
  header[156] = type;
  return Buffer.concat([header, data, Buffer.alloc((512 - data.length % 512) % 512)]);
}

test('extracts a regular uv file from TAR without writing archive paths', () => {
  const buffer = gzipSync(Buffer.concat([
    tarEntry('../outside', Buffer.from('ignored')),
    tarEntry('uv-mac/uv', Buffer.from('test-binary')),
    Buffer.alloc(1024),
  ]));
  assert.equal(extractExecutable(buffer, 'uv-mac.tar.gz').toString(), 'test-binary');
  assert.throws(() => extractExecutable(gzipSync(tarEntry('uv-mac/uv', Buffer.from('link'), 50)), 'uv-mac.tar.gz'), /格式无效/);
});

test('parallel bootstraps serialize, and a failed setup releases its lock', async () => {
  const folder = await mkdtemp(path.join(tmpdir(), 'flight-mcp-lock-'));
  const lock = path.join(folder, 'install.lock');
  const events = [];
  try {
    const first = withLock(lock, async () => {
      events.push('start');
      await new Promise(resolve => setTimeout(resolve, 60));
      events.push('end');
    });
    await new Promise(resolve => setTimeout(resolve, 20));
    await Promise.all([first, withLock(lock, async () => events.push('second'))]);
    assert.deepEqual(events, ['start', 'end', 'second']);
    await assert.rejects(withLock(lock, async () => { throw new Error('interrupted'); }), /interrupted/);
    await withLock(lock, async () => events.push('recovered'));
    assert.equal(events.at(-1), 'recovered');
  } finally { await rm(folder, { recursive: true, force: true }); }
});

test('help, version and MCP config work without downloading a runtime', () => {
  const cli = path.join(packageRoot, 'npm/cli.mjs');
  const env = { ...process.env, FLIGHT_MCP_UV_DOWNLOAD_BASE_URL: 'https://invalid.invalid' };
  const help = spawnSync(process.execPath, [cli, '--help'], { encoding: 'utf8', env });
  assert.equal(help.status, 0, help.stderr);
  assert.match(help.stdout, /setup/);
  const result = spawnSync(process.execPath, [cli, '--data-dir', path.join('中文 空格', 'data'), 'mcp-config'], { encoding: 'utf8', env });
  assert.equal(result.status, 0, result.stderr);
  assert.equal(result.stderr, '');
  const config = JSON.parse(result.stdout).mcpServers['flight-agent'];
  assert.equal(config.command, process.execPath);
  assert.deepEqual(config.args, [cli, '--data-dir', path.resolve('中文 空格', 'data'), 'serve']);
  const custom = spawnSync(process.execPath, [cli, 'mcp-config'], { encoding: 'utf8', env: {
    ...env, FLIGHT_MCP_CACHE_DIR: 'custom cache', PLAYWRIGHT_BROWSERS_PATH: 'custom browsers',
    FLIGHT_MCP_DATA_DIR: 'custom data',
  } });
  assert.equal(custom.status, 0, custom.stderr);
  const customConfig = JSON.parse(custom.stdout).mcpServers['flight-agent'];
  assert.deepEqual(customConfig.env, { FLIGHT_MCP_CACHE_DIR: path.resolve('custom cache'),
    PLAYWRIGHT_BROWSERS_PATH: path.resolve('custom browsers') });
  assert.deepEqual(customConfig.args.slice(-3), ['--data-dir', path.resolve('custom data'), 'serve']);
});

test('the npm executable symlink runs the CLI', { skip: process.platform === 'win32' }, async () => {
  const folder = await mkdtemp(path.join(tmpdir(), 'flight-npm-bin-'));
  try {
    const link = path.join(folder, 'flight-mcp');
    await symlink(path.join(packageRoot, 'npm/cli.mjs'), link);
    const result = spawnSync(process.execPath, [link, '--version'], { encoding: 'utf8' });
    assert.equal(result.status, 0, result.stderr);
    assert.equal(result.stdout.trim(), '0.2.0');
  } finally { await rm(folder, { recursive: true, force: true }); }
});
