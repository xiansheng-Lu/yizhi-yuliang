import { existsSync, readdirSync } from "node:fs";
import { resolve, dirname } from "node:path";
import { fileURLToPath } from "node:url";
import { spawnSync } from "node:child_process";

const root = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const localNode = resolve(
  root,
  ".runtime",
  process.platform === "win32" ? "node.exe" : "node",
);
const node = existsSync(localNode) ? localNode : process.execPath;
const version = spawnSync(node, ["--version"], {
  encoding: "utf8",
}).stdout?.trim();
if (version !== "v24.19.0") {
  console.error(
    "请先运行 powershell -File scripts/setup-runtime.ps1，准备项目级 Node 24.19.0。",
  );
  process.exit(1);
}
const cli = (name) => resolve(root, "node_modules", name);
const commands = {
  install: [
    process.env.npm_execpath ??
      resolve(
        root,
        ".runtime",
        "node-v24.19.0-win-x64/node_modules/npm/bin/npm-cli.js",
      ),
    "ci",
    "--no-audit",
    "--no-fund",
  ],
  dev: [
    cli("next/dist/bin/next"),
    "dev",
    "--webpack",
    "--hostname",
    "127.0.0.1",
    "--port",
    "3000",
  ],
  build: [cli("next/dist/bin/next"), "build", "--webpack"],
  start: [
    cli("next/dist/bin/next"),
    "start",
    "--hostname",
    "127.0.0.1",
    "--port",
    "3000",
  ],
  lint: [cli("eslint/bin/eslint.js"), ".", "--max-warnings=0"],
  typecheck: [cli("typescript/bin/tsc"), "--noEmit"],
  test: [
    cli("tsx/dist/cli.mjs"),
    "--test",
    ...readdirSync(resolve(root, "tests"))
      .filter((x) => x.endsWith(".test.ts"))
      .map((x) => resolve(root, "tests", x)),
  ],
  routes: [resolve(root, "scripts", "check-routes.mjs")],
};
const args = commands[process.argv[2]];
if (!args) throw new Error("Unknown frontend command");
const result = spawnSync(node, [...args, ...process.argv.slice(3)], {
  cwd: root,
  stdio: "inherit",
  env: {
    ...process.env,
    NEXT_TELEMETRY_DISABLED: "1",
    PATH: `${dirname(node)}${process.platform === "win32" ? ";" : ":"}${process.env.PATH}`,
  },
});
if (result.error) console.error(result.error.message);
process.exit(result.status ?? 1);
