# Nix 与 NixOS

仓库提供锁定的 source-built flake。Core、FunASR workers、GTK 设置中心、IBus engine 和 Fcitx 5 Module 都由 Nix 从当前源码构建，不复用 Release 中的预编译二进制。

## 临时运行设置中心

```bash
nix run github:LeonardNJU/VocoType-linux#settings
```

## 构建 flavor

```bash
# universal：IBus + Fcitx 5
nix build github:LeonardNJU/VocoType-linux#vocotype-universal

# Fcitx 5 only
nix build github:LeonardNJU/VocoType-linux#vocotype-fcitx5

# IBus only
nix build github:LeonardNJU/VocoType-linux#vocotype-ibus
```

支持 `x86_64-linux` 和 `aarch64-linux`。FunASR 固定到项目当前验证过的 commit；nixpkgs revision 与 NAR hash 记录在 `flake.lock`。

## NixOS：Fcitx 5

把仓库作为 flake input，并将 Fcitx flavor 加入 addon 列表：

```nix
{
  inputs.vocotype.url = "github:LeonardNJU/VocoType-linux";

  outputs = { self, nixpkgs, vocotype, ... }@inputs: {
    nixosConfigurations.my-host = nixpkgs.lib.nixosSystem {
      system = "x86_64-linux";
      modules = [
        ({ pkgs, ... }: {
          i18n.inputMethod = {
            enable = true;
            type = "fcitx5";
            fcitx5.addons = [
              vocotype.packages.${pkgs.system}.vocotype-fcitx5
            ];
          };

          environment.systemPackages = [
            vocotype.packages.${pkgs.system}.vocotype-fcitx5
          ];
        })
      ];
    };
  };
}
```

VoCoType 是全局 Module，不需要添加为独立输入法。重建并重新登录后，打开 `vocotype-settings` 下载模型、选择麦克风和录制快捷键。

## NixOS：IBus

```nix
{
  i18n.inputMethod = {
    enable = true;
    type = "ibus";
    ibus.engines = [
      inputs.vocotype.packages.${pkgs.system}.vocotype-ibus
    ];
  };

  environment.systemPackages = [
    inputs.vocotype.packages.${pkgs.system}.vocotype-ibus
  ];
}
```

重建后选择 **VoCoType Voice Input** IBus engine。IBus flavor 内置独立 librime session；Rime schema 可在设置中心选择。

## Home Manager / 非 NixOS

可以把相应 package 加入 `home.packages`，但输入法框架仍需能够发现 package 中的 addon/component 路径。NixOS 的 `i18n.inputMethod.fcitx5.addons` 与 `i18n.inputMethod.ibus.engines` 会自动完成这一步；其他发行版上的 standalone Home Manager 可能需要把 package 的 `share/fcitx5`、`lib/fcitx5` 或 `share/ibus/component` 注入框架搜索路径。

## 模型与配置

Nix store 只包含程序和固定依赖，不包含大型语音模型。模型仍由设置中心下载到用户缓存，配置与术语保存在 XDG 用户目录，因此系统重建不会删除它们。
