# 易致·驭量

本仓库用于团队共同开发、代码同步和版本管理。

- GitHub 仓库：https://github.com/xiansheng-Lu/yizhi-yuliang
- 主分支：`main`

## 开始开发

```bash
git clone https://github.com/xiansheng-Lu/yizhi-yuliang.git
cd yizhi-yuliang
git switch -c feat/your-feature
```

完成修改并通过相关检查后提交、推送分支：

```bash
git add <本次修改的文件>
git commit -m "描述本次修改"
git push -u origin feat/your-feature
```

在 GitHub 上创建 Pull Request，由伙伴检查后合并到 `main`。开始新任务前，先更新本地主分支，并从最新的 `main` 创建分支。

## 协作约定

- 每项功能或修复使用单独分支，提交信息说明具体改动。
- 提交前运行项目已有的相关检查；在 Pull Request 中写明改动和验证结果。
- 密钥、密码、真实环境配置和本地生成的文件不提交到仓库；需要的配置用不含真实凭据的示例文件说明。
- 项目代码加入后，补充技术栈、依赖安装、启动命令和测试方法。

仓库所有者可以在 GitHub 的 **Settings → Collaborators** 中邀请伙伴，接受邀请后即可按分配的权限参与开发。
