# VoCoType Linux 文档

VoCoType Linux 的完整使用手册。这里集中维护安装、图形配置、输入法集成、功能说明和排障步骤；[项目主页](https://vocotype-linux.lsamc.website/zh.html) 负责展示产品，[News](https://vocotype-linux.lsamc.website/zh-news.html) 负责发布重大进展。

[开始安装](getting-started/installation.md){ .md-button .md-button--primary }
[查看最新进展](https://vocotype-linux.lsamc.website/zh-news.html){ .md-button }
[前往 GitHub](https://github.com/LeonardNJU/VocoType-linux){ .md-button }

<div class="grid cards" markdown>

-   :material-download-circle:{ .lg .middle } **第一次安装**

    ---

    优先安装 DEB、RPM 或 Arch 软件包，然后在 **VoCoType 设置** 中完成运行时、模型和输入法集成。

    [:octicons-arrow-right-24: 安装与首次配置](getting-started/installation.md)

-   :material-tune-variant:{ .lg .middle } **配置和测试**

    ---

    使用图形设置中心配置术语、ITN、AI 润色，并在 Playground 中测试麦克风、ASR 和编辑链路。

    [:octicons-arrow-right-24: 图形设置中心](guides/settings-center.md)

-   :material-keyboard-outline:{ .lg .middle } **选择输入法框架**

    ---

    Fcitx 5 以全局 Module 工作；IBus 以独立输入法引擎工作，并支持基于 surrounding text 的语音编辑。

    [:octicons-arrow-right-24: Fcitx 5](integrations/fcitx5.md) · [:octicons-arrow-right-24: IBus](integrations/ibus.md)

-   :material-stethoscope:{ .lg .middle } **诊断问题**

    ---

    先运行设置中心里的 Doctor，再按问题类型查看排障文档；提交 Issue 前可生成脱敏支持包。

    [:octicons-arrow-right-24: 常见问题](troubleshooting/faq.md)

</div>

## 推荐阅读顺序

1. [安装与首次配置](getting-started/installation.md)
2. [图形设置中心](guides/settings-center.md)
3. 根据桌面环境阅读 [Fcitx 5](integrations/fcitx5.md) 或 [IBus](integrations/ibus.md)
4. 按需配置 [术语库](guides/terms.md)、[ITN](guides/itn.md) 与 [AI 润色](guides/slm-streaming.md)

!!! tip "文档与代码同步维护"
    文档源文件就在仓库的 `docs/` 中，通过 Git 和 Pull Request 与代码一起更新。项目网站部署时会自动构建本页面，不存在另一份需要手工同步的 Wiki。

## 获取帮助

- [常见问题](troubleshooting/faq.md)
- [GitHub Issues](https://github.com/LeonardNJU/VocoType-linux/issues)
- [版本记录](https://github.com/LeonardNJU/VocoType-linux/blob/master/CHANGELOG.md)
- [项目最新进展](https://vocotype-linux.lsamc.website/zh-news.html)
