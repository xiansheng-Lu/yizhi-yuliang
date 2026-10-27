local cfg=cjson.decode(ARGV[1]); local lane=ARGV[2]; local token=ARGV[3]
local stamp=redis.call('TIME'); local now=tonumber(stamp[1])*1000+math.floor(tonumber(stamp[2])/1000)
local states={}; local epochs={}; local needs={}; local deny=nil; local retry=now
for i=1,2 do
 local row=redis.call('HMGET',KEYS[i+2],'state','epoch','open_until','probe_until','manual','cooldown_until')
 local state=row[1] or 'CLOSED'; epochs[i]=tonumber(row[2]) or 0; states[i]=state
 local until_open=tonumber(row[3]) or 0; local until_probe=tonumber(row[4]) or 0
 local cooldown=tonumber(row[6]) or 0
 if row[5]=='1' then deny='MANUAL_REVIEW'; retry=0; break end
 if cooldown>now then deny='RATE_LIMITED'; retry=math.max(retry,cooldown)
 elseif state=='OPEN' and until_open>now then deny='CIRCUIT_OPEN'; retry=math.max(retry,until_open)
 elseif state=='HALF_OPEN' and until_probe>now then deny='CIRCUIT_PROBING'; retry=math.max(retry,until_probe)
 elseif state=='OPEN' or state=='HALF_OPEN' then
  if lane~='PROBE' then deny=deny or 'PROBE_REQUIRED'; retry=math.max(retry,now+cfg.probe_lease_ms)
  else needs[i]=true end
 end
end
local function result(allowed,reason,next_ms)
 return cjson.encode({allowed=allowed,reason=reason,server_ms=now,retry_at_ms=next_ms,
  app_epoch=epochs[1] or 0,account_epoch=epochs[2] or 0,probe_token=token})
end
if deny then return result(false,deny,retry) end
local window=math.floor(now/cfg.window_ms); local counts={}; local limits={cfg.app_limit,cfg.account_limit}
for i=1,2 do
 local row=redis.call('HMGET',KEYS[i],'window','used')
 counts[i]=(tonumber(row[1])==window) and (tonumber(row[2]) or 0) or 0
 local reserve_read=(i==1) and cfg.app_readback_reserve or cfg.account_readback_reserve
 local reserve_execute=(i==1) and cfg.app_execute_reserve or cfg.account_execute_reserve
 local allowed=limits[i]
 if lane=='SYNC' then allowed=allowed-reserve_read-reserve_execute
 elseif lane=='EXECUTE' then allowed=allowed-reserve_read end
 if allowed<=0 then return result(false,'LANE_DISABLED',0) end
 if counts[i]>=allowed then return result(false,'QUOTA',(window+1)*cfg.window_ms) end
end
for i=1,2 do
 redis.call('HSET',KEYS[i],'window',window,'used',counts[i]+1)
 redis.call('PEXPIRE',KEYS[i],cfg.window_ms*2)
 if needs[i] then
  redis.call('HSET',KEYS[i+2],'state','HALF_OPEN','probe_token',token,'probe_until',now+cfg.probe_lease_ms)
 end
end
return result(true,'ALLOWED',now)
