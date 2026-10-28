/** Actual PG comparison result consumed by the existing TS range/group/metric validators. */
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {createRequire} from 'node:module';
const require=createRequire(import.meta.url);
const {previousRange,groupAnalysisPoints,pointValue}=require('../../frontend/src/lib/business-analysis.ts');
const {validateAnalysisResult}=require('../../frontend/src/services/analysis-service.ts');
const {result}=JSON.parse(readFileSync(process.argv[2],'utf8'));
validateAnalysisResult(result,{shops:[{id:result.query.shopId,connectorId:'adq'}],adAccounts:[],products:[],plans:[],liveSessions:[]});
assert.deepEqual(result.comparison.previous,previousRange(result.query));
const roi=result.metrics.find((m:any)=>m.id==='ads.roi');
assert.deepEqual([roi.value,roi.previous,roi.yearAgo],[3,2,1.5]);
for (const [points,expected] of [[result.previousPoints,2],[result.yearAgoPoints,1.5]] as const) {
  assert.equal(points.length,7);
  for (const group of groupAnalysisPoints(points,true)) assert.equal(pointValue(group.values,roi),expected);
}
console.log(JSON.stringify({result:'PASS',actualPGComparisonResult:true,actualPreviousRange:true,
 actualGroupAnalysisPoints:true,actualPointValue:true,roi:[roi.value,roi.previous,roi.yearAgo],
 previous:result.comparison.previous,yearAgo:result.comparison.yearAgo}));
