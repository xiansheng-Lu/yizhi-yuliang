local cfg=cjson.decode(ARGV[1]); local kind=ARGV[2]; local expected={tonumber(ARGV[3]),tonumber(ARGV[4])}
local probe=ARGV[5]; local retry_ms=tonumber(ARGV[6]); local manual=ARGV[7]=='1'; local rate_scope=ARGV[8]
local stamp=redis.call('TIME'); local now=tonumber(stamp[1])*1000+math.floor(tonumber(stamp[2])/1000)
local alerts=0; local changed=0
if not redis.call('SET',KEYS[7],'1','NX','PX',172800000) then
 return cjson.encode({server_ms=now,changed=0,alert=false})
end
for i=1,2 do
 local statekey=KEYS[i+2]; local row=redis.call('HMGET',statekey,'state','epoch','probe_token','failures')
 local state=row[1] or 'CLOSED'; local epoch=tonumber(row[2]) or 0
 local current=(epoch==expected[i])
 if state=='HALF_OPEN' and row[3]~=probe then current=false end
 if current then
  local alerted=false
  if kind=='RATE_LIMITED' and (rate_scope=='UNKNOWN' or (rate_scope=='APPLICATION' and i==1) or (rate_scope=='ACCOUNT' and i==2)) then
   if manual then redis.call('HSET',statekey,'manual','1')
   else
    local old=tonumber(redis.call('HGET',statekey,'cooldown_until')) or 0
    redis.call('HSET',statekey,'cooldown_until',math.max(old,now+retry_ms))
   end
   alerted=true; changed=changed+1
   if state=='HALF_OPEN' then
    redis.call('HSET',statekey,'state','OPEN','epoch',epoch+1,'open_until',now+cfg.open_ms,'probe_token','','probe_until',0)
   end
  elseif kind=='SUCCESS' then
   if state=='HALF_OPEN' then
    redis.call('HSET',statekey,'state','CLOSED','epoch',epoch+1,'open_until',0,'probe_until',0,'probe_token','','failures',0)
    changed=changed+1
   elseif state=='CLOSED' then redis.call('HSET',statekey,'failures',0) end
  elseif kind=='FAILURE' then
   local failures=(tonumber(row[4]) or 0)+1
   redis.call('HSET',statekey,'failures',failures)
   if state=='HALF_OPEN' or (state=='CLOSED' and failures>=cfg.failure_threshold) then
    redis.call('HSET',statekey,'state','OPEN','epoch',epoch+1,'open_until',now+cfg.open_ms,'probe_until',0,'probe_token','')
    alerted=true; changed=changed+1
   end
  end
  if alerted and redis.call('SET',KEYS[i+4],'1','NX','PX',cfg.alert_quiet_ms) then alerts=alerts+1 end
 end
end
return cjson.encode({server_ms=now,changed=changed,alert=alerts>0})
