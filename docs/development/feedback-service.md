# 官方反馈服务

官方入口为：

```text
https://feedback.vocotype-linux.lsamc.website/v1/feedback
```

桌面客户端发送 `multipart/form-data`，其中 `payload` 是 schema version 1 的 JSON，`bundle` 是可选的脱敏支持包。服务端同时接受旧版 `application/json` 请求以便兼容，但新客户端不再把附件编码成 base64。

## 服务端保证

- 消息最多 10,000 字；Doctor 最多 128 KiB；支持包最多 5 MiB；
- 仅接受 `.tar.gz`、`.tgz` 和 `.zip`，并检查文件魔数；
- 服务端生成附件文件名，附件目录不公开；
- 不保存明文 IP，只保存 HMAC；限流事件两天后清理；
- 同一版本、正文和 Doctor 错误组合在 24 小时内自动合并；
- 同一安装 ID 每小时 3 条、每天 10 条；同一网络每小时 20 条；
- 支持包 30 天后删除；SQLite 每日备份并保留 14 天。

## VPS 布局

```text
/opt/vocotype-feedback/                 应用与虚拟环境
/etc/vocotype-feedback.env             服务 secret，0600
/var/lib/vocotype-feedback/            SQLite 与私有附件
/var/backups/vocotype-feedback/         每日 SQLite 备份
/etc/nginx/sites-available/feedback...  反向代理
```

API 只监听 `127.0.0.1:18088`，Nginx 负责公网 HTTPS 和 7 MiB 请求上限。

## 查看和处理反馈

VPS 上使用：

```bash
sudo vocotype-feedback list --status new
sudo vocotype-feedback show fb_...
sudo vocotype-feedback status fb_... triaged --note "已复现"
sudo vocotype-feedback status fb_... resolved
```

匿名报告不会自动变成公开 GitHub Issue。维护者应先检查敏感信息、去重并确认复现，再人工整理为公开 Issue。
