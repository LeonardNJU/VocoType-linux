# 官方反馈服务

官方入口：

```text
https://feedback.vocotype-linux.lsamc.website/v1/feedback
```

反馈接收端是编译后的 C++ 服务，使用 Boost.Beast、SQLite、OpenSSL 和 nlohmann/json。桌面客户端以 `multipart/form-data` 发送 schema 1 JSON 和可选支持包；服务也接受 `application/json` 及可选 base64 附件以兼容旧客户端。

## 服务端保证

- 消息最多 10,000 字；Doctor 最多 128 KiB；支持包最多 5 MiB；
- 只接受 `.tar.gz`、`.tgz`、`.zip` 并检查魔数；
- 附件使用服务端生成的文件名，目录不公开；
- 不保存明文 IP，只保存 HMAC；限流事件 48 小时后清理；
- 同一版本、正文和 Doctor 错误组合在 24 小时内自动合并；
- 默认每安装 ID 每小时 3 条、每天 10 条，同一网络每小时 20 条；
- 支持包默认 30 天后删除，SQLite 每日备份并保留 14 天。

## VPS 布局

```text
/opt/vocotype-feedback/bin/vocotype-feedback  C++ 服务与 CLI
/etc/vocotype-feedback.env                    secret，0600
/var/lib/vocotype-feedback/                   SQLite 与私有附件
/var/backups/vocotype-feedback/                SQLite 备份
```

服务只监听 `127.0.0.1:18088`，Nginx 负责公网 HTTPS、请求大小限制与反向代理。

## 构建

```bash
cmake -S feedback_service -B build/feedback-service \
  -DCMAKE_BUILD_TYPE=Release -DCMAKE_INSTALL_PREFIX=/opt/vocotype-feedback
cmake --build build/feedback-service -j
sudo cmake --install build/feedback-service
```

## 运维

```bash
sudo vocotype-feedback list --status new
sudo vocotype-feedback show fb_...
sudo vocotype-feedback status fb_... triaged --note "已复现"
sudo vocotype-feedback maintenance --attachment-days 30 \
  --backup-dir /var/backups/vocotype-feedback --backup-days 14
```
