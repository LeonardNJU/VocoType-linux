# macOS构建、签名与DMG

VoCoType-linux的 macOS实现是 InputMethodKit Palette Input Method，用户可见 AppKit设置中心内嵌输入法 payload。用户把一个 App拖入 Applications，首次启动再将输入法部署到 `~/Library/Input Methods`。

## 本地ad-hoc构建

```bash
ALLOW_ADHOC_TEST=1 packaging/macos/build-dmg.sh
```

构建会执行 Core测试、macOS集成测试、FunASR worker构建、依赖闭包重写、嵌套代码签名、运行时 smoke、DMG Finder布局和 `hdiutil verify`。

ad-hoc包没有开发者身份，也不能公证；它可用于开源 Beta分发，但首次启动需要用户在“隐私与安全性”中选择“仍要打开”。

## Developer ID与公证

```bash
CODESIGN_IDENTITY="Developer ID Application: Example (TEAMID)" \
  packaging/macos/build-dmg.sh
```

已有 `notarytool` keychain profile时：

```bash
CODESIGN_IDENTITY="Developer ID Application: Example (TEAMID)" \
NOTARY_PROFILE="vocotype-notary" \
  packaging/macos/build-dmg.sh
```

脚本从内到外签名 dylib、worker、录音器、输入法 bundle、设置 App与 DMG；正式身份启用 hardened runtime和时间戳，并可提交 notarization后 staple票据。

## CI

GitHub Actions使用 `macos-15` arm64 runner，从干净 checkout安装 Homebrew构建依赖，构建 ad-hoc DMG并运行同一套测试。产物作为 `macos-arm64-dmg` artifact进入最终 Release资产集合。

## 身份隔离

产品名称继续使用 VoCoType-linux。macOS bundle ID为 `io.github.LeonardNJU.VoCoTypeLinux.InputMethod`，配置、缓存、进程和输入源均使用独立命名，不触碰其他同名第三方 App。
