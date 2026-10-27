/** 提取真实TypeScript调用与字段声明。--write仅在复核后更新，默认核对漂移。 */
import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import ts from "../../../node_modules/typescript/lib/typescript.js";

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "../../..");
const contracts = path.join(root, "docs/backend/contracts");
const matrix = JSON.parse(fs.readFileSync(path.join(contracts, "http-contracts.json"), "utf8"));
const prefixes = {
  ApiClient: "", DeliveryService: "/v1/delivery/",
  AdvertisingService: "/v1/delivery/configuration/", ApiAnalysisAdapter: "/v1/business-analysis",
};
const config = ts.readConfigFile(path.join(root, "tsconfig.json"), ts.sys.readFile);
const parsed = ts.parseJsonConfigFileContent(config.config, ts.sys, root);
const program = ts.createProgram({
  rootNames: Object.keys(matrix.sourceSha256).map(name => path.join(root, name)),
  options: parsed.options,
});
const checker = program.getTypeChecker();
const calls = [];
const types = [];
const actualRoutes = new Set();
const canonical = (method, url) => `${method} ${url.split("?")[0].replace(/\{[^}]*\}/g, "{}")}`;
function pathText(node) {
  if (ts.isStringLiteral(node) || ts.isNoSubstitutionTemplateLiteral(node)) return node.text;
  if (ts.isTemplateExpression(node)) return node.head.text + node.templateSpans.map(
    span => `{${span.expression.getText()}}${span.literal.text}`).join("");
  throw new Error(`未识别的具体请求路径：${node.getText()}`);
}
function routePairs(pathNode, methodNode) {
  if (ts.isConditionalExpression(pathNode)) {
    if (methodNode && ts.isConditionalExpression(methodNode) &&
        pathNode.condition.getText() === methodNode.condition.getText()) {
      return [...routePairs(pathNode.whenTrue, methodNode.whenTrue),
        ...routePairs(pathNode.whenFalse, methodNode.whenFalse)];
    }
    return [...routePairs(pathNode.whenTrue, methodNode), ...routePairs(pathNode.whenFalse, methodNode)];
  }
  return [[methodNode ? pathText(methodNode) : "GET", pathText(pathNode)]];
}
for (const relative of Object.keys(matrix.sourceSha256).sort()) {
  const source = program.getSourceFile(path.join(root, relative));
  assert(source, `无法解析${relative}`);
  function visit(node) {
    if ((ts.isInterfaceDeclaration(node) || ts.isTypeAliasDeclaration(node)) &&
        node.modifiers?.some(modifier => modifier.kind === ts.SyntaxKind.ExportKeyword)) {
      types.push({ source: relative, line: source.getLineAndCharacterOfPosition(node.getStart()).line + 1,
        name: node.name.text, definition: node.getText(source) });
    }
    if (ts.isClassDeclaration(node) && node.name && node.name.text in prefixes) {
      for (const method of node.members) {
        if (!ts.isMethodDeclaration(method) || !method.body) continue;
        const methodName = method.name.getText(source);
        function readCall(child) {
          if (ts.isCallExpression(child) && ts.isPropertyAccessExpression(child.expression) &&
              child.expression.expression.kind === ts.SyntaxKind.ThisKeyword &&
              ["request", "transport", "job"].includes(child.expression.name.text)) {
            const job = child.expression.name.text === "job";
            const args = child.arguments;
            const body = args[job ? 1 : 2];
            const forwarding = ["analysisRequest", "deliveryRequest", "job"].includes(methodName);
            if (!forwarding) {
              for (const [verb, url] of routePairs(args[0], job ? ts.factory.createStringLiteral("POST") : args[1])) {
                actualRoutes.add(canonical(verb, prefixes[node.name.text] + url));
              }
            }
            calls.push({ source: relative, line: source.getLineAndCharacterOfPosition(child.getStart()).line + 1,
              class: node.name.text, method: methodName, prefix: prefixes[node.name.text],
              path: args[0]?.getText(source),
              httpMethod: job ? '"POST"' : args[1]?.getText(source) ?? '"GET"',
              body: body?.getText(source) ?? null,
              requestType: body ? checker.typeToString(checker.getTypeAtLocation(body), body,
                ts.TypeFormatFlags.NoTruncation) : null,
              responseType: child.typeArguments?.map(type => type.getText(source)).join(", ") ?? "CommandJob",
              forwardingHelper: forwarding,
            });
          }
          ts.forEachChild(child, readCall);
        }
        readCall(method.body);
      }
    }
    ts.forEachChild(node, visit);
  }
  visit(source);
}
assert(calls.some(call => call.class === "ApiClient" && call.method === "reviewJob"));
assert(calls.some(call => call.class === "AdvertisingService" && call.method === "uploadCreative"));
assert(calls.some(call => call.class === "ApiAnalysisAdapter" && call.method === "listResults"));
assert(calls.some(call => call.class === "DeliveryService" && call.method === "changePlan"));
const expectedRoutes = new Set(Object.values(matrix.routes).flat().map(([method, url]) => canonical(method, url)));
assert.deepEqual([...actualRoutes].sort(), [...expectedRoutes].sort(), "人工矩阵与实际调用不一致");
const result = { baseline: matrix.technicalRefresh?.baseline ?? "4405ebc", extraction: "TypeScript AST and type checker; no HTTP/platform execution",
  calls, exportedTypes: types };
const target = path.join(contracts, "frontend-surface.json");
const serialized = JSON.stringify(result,
  (_key, value) => typeof value === "string" ? value.replace(/\r\n/g, "\n") : value, 2) + "\n";
if (process.argv.includes("--write")) fs.writeFileSync(target, serialized, "utf8");
else assert.equal(fs.readFileSync(target, "utf8").replace(/\r\n/g, "\n"), serialized,
  "前端调用或字段有变化，需复核后才允许--write更新冻结基线");
for (const [name, expected] of Object.entries(matrix.sourceSha256)) {
  const normalized = fs.readFileSync(path.join(root, name), "utf8").replace(/\r\n/g, "\n");
  assert.equal(createHash("sha256").update(normalized).digest("hex"), expected, `前端源码漂移：${name}`);
}
console.log(JSON.stringify({ result: "PASS", callSites: calls.length, exportedTypes: types.length,
  routes: actualRoutes.size, frozenSources: Object.keys(matrix.sourceSha256).length,
  write: process.argv.includes("--write") }));
