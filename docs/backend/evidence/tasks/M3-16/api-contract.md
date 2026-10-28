# 内容校验错误接线与规则决议

不新增业务路由。文档PUT仍ContentDocument/version，ApiError信封不变；非法内容422/INVALID_INPUT，权限仍沿FORBIDDEN等既有安全语义，不将授权失败伪装字段错误。

```json
{"requestId":"...","error":{"code":"INVALID_INPUT","message":"内容校验未通过","details":[{"field":"document.boards.0.layers.0.fontSize","reason":"需要有限数值","documentId":"UUID","boardId":"UUID","layerId":"UUID"}],"retryable":false}}
```

details的field是完整规范路径、reason为受控中文、对象ID经过有限标识规则提取。不给input、ctx、异常msg、正文、字段原值或不支持字段名。无效对象ID可能只给字段路径，不回显无效值。

环错误定位到具体edge的targetNodeId/edgeId；已有caption片段空白text定位document.clips.<index>.text/clipId并拒绝。x/y保持前端兼容的有限0/负，宽高/字号正数；该跨册差异待BE/FE具名收口。skill标签不代表执行授权。

三purpose仅共用库调用协议；当前不存在生成/SOP完整业务入口，F09/F10不能把本卡错误样例当作这些业务已开放。音视频仍未支持，U层合成可信时长事实不接收自客户端。
