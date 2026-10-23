import { readFileSync, existsSync } from "node:fs";
import { createHash } from "node:crypto";
import { resolve } from "node:path";
const baseline = JSON.parse(
  readFileSync(new URL("../docs/shared/baselines/homepage-baseline.json", import.meta.url)),
);
const hash = (file) =>
  createHash("sha256").update(readFileSync(file)).digest("hex");
for (const [name, expected] of Object.entries(baseline.files)) {
  const target = resolve("public", name),
    source = resolve(baseline.sourceRoot, name);
  if (
    hash(target) !== expected ||
    (existsSync(source) &&
      hash(source) !== (baseline.sourceFiles?.[name] ?? expected))
  )
    throw new Error(`Frozen homepage changed: ${name}`);
  console.log(`PASS ${name} ${expected}`);
}
