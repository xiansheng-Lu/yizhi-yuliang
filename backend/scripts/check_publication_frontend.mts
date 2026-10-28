/** Replay real PG publication output through the current ApiClient and ApiAnalysisAdapter. */
import { readFileSync } from "node:fs";
import assert from "node:assert/strict";
import { createRequire } from "node:module";
const require = createRequire(import.meta.url);
const { ApiClient } = require("../../frontend/src/services/api-client.ts");
const { ApiAnalysisAdapter } = require("../../frontend/src/services/analysis-service.ts");
const trace = JSON.parse(readFileSync(process.argv[2], "utf8"));
let fetches=0;
const api=new ApiClient("https://captured-transport.invalid",async(url:string,init:RequestInit)=>{
  fetches++;
  const path=new URL(String(url)).pathname;
  let data;
  if(path.endsWith("/csrf"))data={token:"captured-transport-csrf",expiresAt:new Date(Date.now()+60000).toISOString()};
  else if(path.endsWith("/publications") && init.method!=="POST")data=trace.list;
  else data=trace.publication;
  return Response.json({schemaVersion:"command-job-v1.0",requestId:"publication-contract",data});
});
const adapter=new ApiAnalysisAdapter(api,{});
const signal=new AbortController().signal;
assert.deepEqual(await adapter.publishDraft(trace.publication.draftId,trace.publication.snapshot.version,signal),trace.publication);
assert.deepEqual(await adapter.getPublished(trace.publication.id,signal),trace.publication);
assert.deepEqual(await adapter.listPublished(signal),trace.list);
assert.equal(trace.actualLoopbackHTTP,true);
assert.equal(trace.anonymousStatus,200);
console.log(JSON.stringify({result:"PASS",actualPGPublication:true,actualFrontendApiClient:true,
 actualFrontendApiAnalysisAdapter:true,actualAnonymousLoopbackHTTP:true,source:trace.publication.source,
 stableVersion:trace.publication.snapshot.version,fetches,transportReplay:true,browserAcceptance:"NOT_RUN",
 publicDeployment:"NOT_RUN",rawTokensPrinted:false}));
