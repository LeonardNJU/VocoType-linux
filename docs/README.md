# VoCoType Linux 文档

VoCoType-linux 的完整使用手册。项目历史名称保持不变，但 V5 已原生支持 Linux 与 macOS。这里集中维护安装、图形配置、输入法集成、功能说明和排障步骤；[项目主页](https://vocotype-linux.lsamc.website/zh.html) 负责展示产品，[News](https://vocotype-linux.lsamc.website/zh-news.html) 负责发布重大进展。

## 快速入口

- [macOS安装、升级与Gatekeeper](getting-started/macos.md)
- [Linux安装与首次配置](getting-started/installation.md)
- [图形设置中心](guides/settings-center.md)
- [Fcitx 5集成](integrations/fcitx5.md)
- [IBus集成](integrations/ibus.md)
- [用户词典与原生热词](guides/terms.md)
- [常见问题](troubleshooting/faq.md)
- [GitHub仓库](https://github.com/LeonardNJU/VocoType-linux)

## 推荐阅读顺序

1. macOS用户阅读 [macOS安装指南](getting-started/macos.md)；Linux用户阅读 [Linux安装指南](getting-started/installation.md)。
2. 阅读 [图形设置中心](guides/settings-center.md)。
3. Linux用户根据桌面环境阅读 [Fcitx 5](integrations/fcitx5.md) 或 [IBus](integrations/ibus.md)。
4. 按需配置 [术语库](guides/terms.md)、[ITN](guides/itn.md)、[实时识别预览](guides/asr-streaming.md) 与 [AI润色](guides/slm-streaming.md)。
5. 使用语音编辑前阅读 [语音编辑兼容性与局限](guides/voice-editing.md)。

## 文档与代码同步

文档源文件位于仓库 `docs/`。GitHub Pages 使用编译后的 C++静态文档生成器，将 Markdown与 `web/`官网资源合并为 HTML；没有另一份需要手工同步的 Wiki，也不需要 Python / MkDocs。
