/** Consume an actual PG-produced AnalysisResult through the existing frontend implementation. */
import { readFileSync } from 'node:fs';
import assert from 'node:assert/strict';
import { createRequire } from 'node:module';
const require=createRequire(import.meta.url);
const { groupAnalysisPoints,pointValue }=require('../../frontend/src/lib/business-analysis.ts');
const { validateAnalysisResult }=require('../../frontend/src/services/analysis-service.ts');
const trace=JSON.parse(readFileSync(process.argv[2],'utf8'));
const result=trace.result;
const snapshot={shops:[{id:result.query.shopId,connectorId:'wxshop'}],adAccounts:[{shopId:result.query.shopId,connectorId:'adq'}],products:[],plans:[],liveSessions:[]};
validateAnalysisResult(result,snapshot);
const metrics=new Map(result.metrics.map((m:any)=>[m.id,m]));
const grouped=groupAnalysisPoints(result.points,true);
assert.equal(grouped.length,1);
const roi=pointValue(grouped[0].values,metrics.get('ads.roi'));
const ctr=pointValue(grouped[0].values,metrics.get('ads.ctr'));
const cpc=pointValue(grouped[0].values,metrics.get('ads.cpc'));
const cpa=pointValue(grouped[0].values,metrics.get('ads.cpa'));
assert.equal(roi,1.9);
assert.notEqual(roi,11);
assert.equal(ctr,35/140);
assert.ok(Math.abs(cpc-1000/35)<0.000001);
assert.equal(cpa,null);
console.log(JSON.stringify({result:'PASS',actualPGResult:true,actualFrontendGroupAnalysisPoints:true,
 actualFrontendPointValue:true,weeklyROI:roi,weeklyCTR:ctr,weeklyCPC:cpc,missingCPA:cpa,
 source:result.source,schemaVersion:result.schemaVersion}));
