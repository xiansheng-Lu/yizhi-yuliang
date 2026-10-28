# M3-20a 账号交接意图API

当前为实际后端API；F09/F11云端适配与真实浏览器待联调。所有入口要求真实Cookie和X-Shop-Id；POST额外要求X-CSRF-Token。禁止未知query覆盖身份。

- GET `/v1/content/assets/{asset}/account-links`：当前素材和账户可读范围的状态数组（包括取消记录当前状态）；1000上限，存在1001条整体422，禁止截断。
- POST 同路径：`{productLine,accountId,expectedVersion}`。新意图expectedVersion=0；同键活动意图重复请求复用原linkedAt/linkedBy；取消后重连带当前版本。主体/原生ID/时间/actor来自服务器，不接受客户端快照。
- POST `/{asset}/account-links/{id}/cancel`：`{expectedVersion}`。取消只改变意图状态+历史，不HEAD、不恢复OAuth、不删文件/平台证据。当前店铺/商品管理权限仍必须有。

返回每项为id/version/intent/link/cancelledAt/assetVersion/upload/review。
`link`完整沿用冻结ContentAccountLink字段：accountId/accountName/nativeId/productLine/businessEntityId/tenantId/shopId/mode=API/linkedAt/linkedBy。
`intent=LINKED|CANCELLED`是本地业务意图；`upload.status=NOT_INTEGRATED, platformAssetId=null`；`review.status=UNKNOWN`并给来源缺口原因。不得由调用者修改upload/review或把关联状态当平台上传成功。

同键旧版本或原生/素材快照变化409；无当前权限403；缺文件证明/目标OAuth或scope不满足明确受控错误；返回采用现有success/error envelope与requestId。账户授权再校验用于意图范围，不是UPLOAD许可。
