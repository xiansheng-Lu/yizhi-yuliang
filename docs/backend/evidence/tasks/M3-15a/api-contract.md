# M3-15a 上传接口接线

新增云素材接口，与V9平台material-upload-tasks分离。所有请求Cookie登录、X-Shop-Id；写请求X-CSRF-Token。默认blob_enabled=false时422，不隐式调用外部存储。

`POST /v1/content/uploads`：

```json
{"clientRequestKey":"UUID","name":"图片.png","kind":"image","productId":"内部商品UUID或空串","documentId":null,"mime":"image/png","size":79,"sha256":"64位小写SHA256"}
```

类型image只收PNG/JPEG/WebP、text只收text/plain（实际UTF8）；当前audio/video422待探测卡。服务端按当前店铺/商品/文档与身份核对；documentId若提供，商品范围须匹配文档。禁止object_key/bucket/tenant/user等额外字段。

成功data含uploadId、state、expiresAt、asset:null、upload:{method:"PUT",url,headers}。按返回唯一URL上传文件原始字节，并保留签名要求的Content-Type/x-amz-checksum-sha256/x-amz-meta-upload-id；Content-Length绑定计划实际字节数，浏览器应由File/Blob自动生成，不能把multipart包装体上传到该PUT。URL视为短时凭据，不发公共日志、不持久保存。

`GET /v1/content/uploads/{uploadId}` 查询原计划；有效期内可按同一范围重新取得短签名，已CONFIRMED返回asset且不再发PUT URL。原clientRequestKey+相同元数据返回原计划，变更文件/商品/文档/名称等409。

`POST /v1/content/uploads/{uploadId}/complete`：空body，只接原ID，不接客户端key/桶/校验结果。未上传或存储故障502；实际大小/MIME/SHA/解码不符422；正在校验423；过期409；越范围403。成功data为同一ContentAsset metadata（status=CONFIRMED），不含对象key或下载URL；重复返回同一asset。

完成metadata示意：

```json
{"id":"UUID","name":"图片.png","kind":"image","productId":"UUID","documentId":null,"mime":"image/png","size":79,"duration":0,"durationState":"NOT_APPLICABLE","createdAt":"ISO8601","status":"CONFIRMED"}
```

本卡不开放文档assetId引用和公共下载，F09不能把本地assetId当云对象。音视频时长/类型探测、已确认素材列表/下载/引用检查归后续卡。
