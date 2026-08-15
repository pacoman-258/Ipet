# Ipet STS2 Bridge

这是 Ipet 的《杀戮尖塔 2》只读本地模组。它只把白名单事件和有界快照发送到 Ipet 桌面端发布的
本机回环地址，找不到地址时回退到 `http://127.0.0.1:8008`；它不修改存档、不替玩家操作，也不依赖 BaseLib。

## 构建

需要 .NET 9 SDK。仓库不会包含或复制游戏程序集；构建时通过属性引用本机安装：

```bash
dotnet build -p:Sts2InstallDir="/path/to/Slay the Spire 2"
dotnet run --project SelfTest/IpetSts2Bridge.SelfTest.csproj -p:Sts2InstallDir="/path/to/Slay the Spire 2"
```

macOS 的默认 Steam 安装可以这样指定：

```bash
dotnet build -p:Sts2InstallDir="$HOME/Library/Application Support/Steam/steamapps/common/Slay the Spire 2"
```

把 `bin/Debug/net9.0/` 中的 `IpetSts2Bridge.dll` 和 `IpetSts2Bridge.json` 放进游戏加载器使用的
`mods/IpetSts2Bridge/`，然后在游戏模组设置中启用。macOS 的本地模组根目录位于
`SlayTheSpire2.app/Contents/MacOS/mods/`，不是用户数据目录中的 `Application Support/SlayTheSpire2/mods/`。
部署到游戏目录不属于普通构建步骤；本仓库的构建命令不会写入 Steam 目录。

## 兼容策略

战斗、生命、奖励、商店和房间观察优先走游戏公开的 `ModHelper` Run/Combat Hook。只有新 Run、结局和
公开 Hook 尚未覆盖的选择界面使用独立 Harmony Postfix。某个方法在 Early Access 更新中消失时，
对应补丁会被跳过；Run 初始化或公开 Hook 注册失效时，心跳会报告 `compatible=false`。所有观察、
序列化和 HTTP 失败都会被模组边界吞掉，Ipet 保持安静，游戏流程不受影响。
