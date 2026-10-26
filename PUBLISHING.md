# 发布到 Caspian-Li GitHub

目标仓库名：`raw-rag-aml`。当前本地项目不等同于该 GitHub 仓库已经存在或已收到 push。创建/推送需要账号的有效授权，个人主页链接不提供写入权限。

## 从现有本地项目推送

先在 GitHub 的 Caspian-Li 账号创建公开空仓库 `raw-rag-aml`，不要添加自动生成的 README/License，然后在已认证 Git 的终端执行：

```bash
cd /lihanrong/raw-rag-aml
git remote -v
# 目标 origin 应为 https://github.com/Caspian-Li/raw-rag-aml.git
git push -u origin main
```

不要把 token 写进 remote URL，不要上传 .env、SQLite、旧评测结果或日志。发布前运行 `python scripts/verify_release.py`。公开文件由本地 Git 跟踪清单控制。

## 从 Git bundle 恢复

若在其他电脑操作，将交付的 `.bundle` 文件复制过去：

```bash
git clone /实际路径/raw-rag-aml-<提交短SHA>.bundle raw-rag-aml
cd raw-rag-aml
git remote set-url origin https://github.com/Caspian-Li/raw-rag-aml.git
git push -u origin main
```

Git bundle 保留本地 commit 与完整源码；tar.gz 是部署源包。不要把源码 tar.gz 作为 GitHub 仓库中唯一一个压缩文件，这样不能直接审核代码或执行 CI。

上传后检查 Actions 是否通过。首次远端 CI 会构建 Docker 并做容器启动检查，之后仍需真实供应商、公网部署和官方 Smoke。GitHub Pages 不支持运行本 Python API，上传成功不会自动产生 Add/Search 服务地址。
