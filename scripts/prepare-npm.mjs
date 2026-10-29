import { createHash } from 'node:crypto';
import { spawnSync } from 'node:child_process';
import { existsSync } from 'node:fs';
import { copyFile, mkdir, readFile, readdir, rm, writeFile } from 'node:fs/promises';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const root = fileURLToPath(new URL('../', import.meta.url));
const pkg = JSON.parse(await readFile(path.join(root, 'package.json'), 'utf8'));
const localPython = path.join(root, '.venv', process.platform === 'win32' ? 'Scripts/python.exe' : 'bin/python');
const python = process.env.FLIGHT_MCP_BUILD_PYTHON || (existsSync(localPython) ? localPython : process.platform === 'win32' ? 'python' : 'python3');
const output = path.join(root, 'build', 'npm-wheel');
await rm(output, { force: true, recursive: true });
await mkdir(output, { recursive: true });
const result = spawnSync(python, ['-m', 'pip', 'wheel', '--no-deps', '--wheel-dir', output, root], {
  cwd: root, stdio: ['ignore', 2, 2], windowsHide: true,
  env: { ...process.env, SOURCE_DATE_EPOCH: process.env.SOURCE_DATE_EPOCH || '1767225600' },
});
if (result.error || result.status !== 0) throw result.error || new Error('构建 Python wheel 失败');
const wheels = (await readdir(output)).filter(name => name.endsWith('.whl'));
if (wheels.length !== 1 || !wheels[0].startsWith(`flight_agent_mcp-${pkg.version}-`)) {
  throw new Error('npm 与 Python 版本必须一致');
}
const payload = path.join(root, 'npm', 'payload');
await rm(payload, { force: true, recursive: true });
await mkdir(payload, { recursive: true });
await copyFile(path.join(output, wheels[0]), path.join(payload, wheels[0]));
// Constraints pin known versions while allowing OS-specific dependencies to resolve.
const lock = await readFile(path.join(root, 'requirements.lock'), 'utf8');
await writeFile(path.join(payload, 'requirements.txt'), lock);
const sha = data => createHash('sha256').update(data).digest('hex');
await writeFile(path.join(payload, 'manifest.json'), JSON.stringify({
  version: pkg.version, wheel: wheels[0], sha256: sha(await readFile(path.join(output, wheels[0]))),
  requirementsSha256: sha(lock),
}, null, 2) + '\n');
console.error(`npm payload: ${wheels[0]}`);
