import { readFileSync, writeFileSync, mkdirSync } from "node:fs";
import { createHash } from "node:crypto";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

// Parse JSON literals only. Never execute scripts embedded in reference HTML.
const root = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const sources = [
  {
    platform: "tencent",
    file: "腾讯营销开放平台API接口全景看板.html",
    variable: "DATA",
    version: "Marketing API 3.0",
  },
  {
    platform: "ocean",
    file: "巨量引擎开放平台API接口全景看板.html",
    variable: "DATA",
    version: "图谱未统一标注版本",
  },
  {
    platform: "wxshop",
    file: "微信小店API全量接口图谱.html",
    variable: "RAW",
    version: "2026-10-21",
  },
  {
    platform: "xhs",
    file: "小红书开放平台API接口全景看板.html",
    variable: "DATA",
    version: "聚光 Marketing API",
  },
];
const normalized = JSON.parse(
  readFileSync(
    resolve(root, "../docs/shared/reference/platform-api-reference/四平台开放API_规范化目录.json"),
    "utf8",
  ),
);
const catalogue = {
  schemaVersion: "platform-catalog-v1",
  provenance:
    "规范化目录与四份原始 HTML 的全部记录逐条对照提取；所有账户授权与运行能力均未声明。",
  sources: [],
  records: [],
};
for (const source of sources) {
  const html = readFileSync(
    resolve(root, "../docs/shared/reference/platform-api-reference", source.file),
    "utf8",
  ).replace(/\r\n/g,"\n");
  const match = html.match(
    new RegExp(`(?:const|var)\\s+${source.variable}\\s*=\\s*(\\{[^\\n]*\\});`),
  );
  if (!match) throw Error(`No JSON literal: ${source.file}`);
  const data = JSON.parse(match[1]);
  const rows = data.records ?? data.apis;
  const groups = data.domains ?? data.doms;
  if (!Array.isArray(rows) || !rows.length)
    throw Error(`No API records: ${source.file}`);
  const seen = new Map();
  const normalizedRows = normalized.interfaces.filter(
    (r) => r.provider === source.platform,
  );
  if (normalizedRows.length !== rows.length)
    throw Error(`Catalogue count mismatch: ${source.file}`);
  for (const [index, r] of rows.entries()) {
    const nativePath = String(r.p ?? r.path ?? "")
      .replaceAll("`", "")
      .trim();
    const normalizedRow = normalizedRows[index];
    const expected =
      source.platform === "tencent"
        ? `/v3.0/${nativePath}`
        : source.platform === "xhs" && nativePath
          ? `/api/open${nativePath}`
          : nativePath;
    if ((normalizedRow.path ?? "") !== expected)
      throw Error(`Path mismatch: ${source.file} #${index}`);
    const identity = [
      source.platform,
      groups?.[r.g]?.name ?? r.category,
      r.m ?? "",
      r.n ?? r.name ?? r.d ?? "",
      nativePath,
    ].join("|");
    const duplicate = seen.get(identity) ?? 0;
    seen.set(identity, duplicate + 1);
    catalogue.records.push({
      id: createHash("sha256")
        .update(`${identity}|${duplicate}`)
        .digest("hex")
        .slice(0, 20),
      platform: source.platform,
      domain: groups?.[r.g]?.name ?? r.category ?? "未分类",
      module: r.m ?? r.category ?? "未分类",
      name: normalizedRow.displayName || r.d || r.name || r.n || nativePath,
      nativePath,
      normalizedPath: normalizedRow.path ?? null,
      method: normalizedRow.method ?? null,
      scopes: Array.isArray(normalizedRow.scope)
        ? normalizedRow.scope
        : normalizedRow.scope
          ? [String(normalizedRow.scope)]
          : [],
      pathEvidence:
        normalizedRow.pathListedAsVerified === true
          ? "SOURCE_VERIFIED"
          : nativePath
            ? "LISTED"
            : "UNCONFIRMED",
      docUrl: r.docUrl ?? null,
      sourceFile: source.file,
    });
  }
  catalogue.sources.push({
    ...source,
    sha256: createHash("sha256").update(html).digest("hex"),
    recordCount: rows.length,
    sourceVerifiedCount: rows.filter((r) => r.ok === 1).length,
    metadata: data.meta ?? null,
  });
}
mkdirSync(resolve(root, "src/data"), { recursive: true });
writeFileSync(
  resolve(root, "src/data/platform-api-catalog.json"),
  JSON.stringify(catalogue, null, 2) + "\n",
);
console.log(
  JSON.stringify(
    {
      total: catalogue.records.length,
      sources: catalogue.sources.map((s) => ({
        file: s.file,
        records: s.recordCount,
        sourceVerified: s.sourceVerifiedCount,
      })),
    },
    null,
    2,
  ),
);
