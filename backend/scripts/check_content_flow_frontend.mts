/** Compare real PG-derived documents with the existing frontend flowDocument implementation. */
import {readFileSync} from "node:fs";
import assert from "node:assert/strict";
import {createRequire} from "node:module";
const require=createRequire(import.meta.url);
const {flowDocument,validateContent}=require("../../frontend/src/lib/content-factory.ts");
const trace=JSON.parse(readFileSync(process.argv[2],"utf8"));
function normalize(doc:any){
 const value=structuredClone(doc);
 value.id="new-document";value.updatedAt="server-time";
 value.scenes.forEach((s:any,i:number)=>s.id="new-scene-"+i);
 value.nodes.forEach((n:any,i:number)=>n.id="new-node-"+i);
 value.boards.forEach((b:any,i:number)=>b.id="new-board-"+i);
 return value;
}
for(const [kind,actual] of Object.entries(trace.children)){
 const expected=flowDocument(trace.source,kind);
 assert.deepEqual(normalize(actual),normalize(expected));
 validateContent({schema:1,revision:0,documents:[actual],assets:[]},[]);
}
console.log(JSON.stringify({result:"PASS",actualPGChildren:5,actualFrontendFlowDocument:true,
 actualFrontendValidateContent:true,browserAcceptance:"NOT_RUN",platformCalls:0}));
