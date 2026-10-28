# F11 资源上下文交接

真实读取入口为`/v1/delivery/integrations/context?productLine=adq`，实际PlatformService已用接口归档通过消费；没有运行新的浏览器场景。上下文保留真实主体、账户和店铺，不把多对多关系压成单个shopId，也不把一个主体ID改写为另一个。

当前PlatformService/assertScope要求选定主体同时匹配账户和店铺；后端保留的跨主体内部关联不能自动据此投放，现有客户端会在HTTP之前拒绝不匹配选择。跨主体操作的业务许可与契约仍待BE/F11确认，不能因目录存在就推断许可。

operations没有单独shopIds字段，后端按账户的全部可见店铺检查对应权限；任一可见店铺只有snapshot.read、缺该操作权限时，该操作关闭。小店经营读取在当前契约中不选择账户，其店铺级操作范围需M3-11b逐请求校验，当前不开放资源入口。

目录行与已知方法/路径、平台授权与内部BOUND关联、通用写内核与资源命令入口分别呈现。当前资源写仍关闭；不要根据editableFields非空启用按钮，应继续使用完整七阶段与当前permission检查。上下文过期或资源变更后重新读取，不能复用别的产品线/native/internal ID。

此卡只交付上下文及逐项依据。资源queries/previews/commands、原键恢复/撤回仍归M3-11b及其技术依赖；没有把它们记作已实现。
