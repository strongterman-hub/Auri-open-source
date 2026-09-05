# 首次开源快照与验证范围

日期：2026-09-05。Android 基线为 **0.3.25 / versionCode 28**。

本说明记录首次开源快照的本地准备结果。源码已于 2026-09-05 发布到 [GitHub 公开仓库](https://github.com/strongterman-hub/Auri-open-source)，首次源码提交为 `c27af81`。本次发布没有产生新的官方 APK，也没有部署生产服务。

推送会触发服务端测试和 Android 构建，最新远程结果见 [GitHub Actions](https://github.com/strongterman-hub/Auri-open-source/actions/workflows/ci.yml)。下表记录本地验证；远程状态以对应提交的运行记录为准。

## 源码范围

服务端与 Android 放在同一个仓库的 `server/`、`android/` 中，保留当前完整业务实现：聊天与异步回复、记忆、主动消息、工具、健康、账号、Credits、支付宝、管理后台、官网及直装更新。

这是从当前工作区整理的新快照，未携带旧 Git 历史。官方运行数据、配置凭据、签名文件、私人运维脚本、缓存和构建产物未纳入发行内容。网站仅保留实际使用、已经公开展示的资源。

服务端与根目录原创文档/工具采用 AGPL-3.0-only，Android 原创代码采用 MIT；第三方来源及许可证见 [第三方声明](../THIRD_PARTY_NOTICES.md)。

## 为自部署作出的调整

- 提供不需要模型 Key 的 Echo 示例配置，默认关闭邮件验证、扣费、支付、主动调度和云健康同步；真实能力按配置启用。
- Android 默认使用社区包名，Debug 连接模拟器宿主机，Release 地址由实例维护者配置。
- 推送 Key 改为可选构建参数；没有 Key 时跳过 JPush 初始化。正式签名由维护者自行提供，没有签名配置也可构建未签名 Release。
- 补充服务端 Python 包发现及静态资源配置，支持按文档进行 editable 安装。
- 全新 Windows 环境发现缺少 IANA 时区数据库会导致启动失败，已显式加入 `tzdata` 依赖。
- 补充中英文介绍、产品与架构、配置默认值、两端开发、部署、贡献、安全反馈和持续集成配置。
- 开源快照的官网页尾增加源码入口；部署修改版时，运营者需要把该入口指向实际运行版本的对应源码。

## 本地验证结果

| 验证项 | 结果 |
|---|---|
| 全新 Python 3.12 虚拟环境安装 `server[dev]` | 通过 |
| 服务端完整测试集 | 255 passed；存在上游测试工具的两项弃用警告 |
| 本地 HTTP Smoke | 健康检查、随机测试账号注册、会话、同步回复、异步回复、幂等重试通过 |
| Android `testStoreDebugUnitTest` | 7 tests，0 failures |
| Android `assembleStoreDebug` | 通过 |
| Android `assembleDirectDebug` | 通过 |
| Android `assembleStoreRelease` | 通过；未使用官方签名 |
| Android `bundleStoreRelease` | 通过；未使用官方签名 |

Android 构建环境为 JDK 17、Android SDK 35、Gradle 8.9。构建中存在现有 Kotlin/Gradle 弃用提示及工具版本提示，未阻塞产物生成。

提交前另外检查 Git 跟踪文件中的常见密钥模式、私有配置凭据匹配、私人邮箱/机器路径、误入库产物和文档相对链接。检查脚本不能替代后续安全审计；发现问题请按 [安全反馈说明](../SECURITY.md) 处理。

## 验证边界

本轮未进行设备安装、模拟器 UI 操作或物理真机验证。Echo 联调不证明真实模型、小米账号、邮件、支付、系统推送及厂商通道已经在新实例可用；这些需要维护者配置自己的服务后单独验收。

未签名 Release 和 AAB 的构建成功不等于可以直接安装、已提交商店或已通过商店审核。GitHub Actions 的具体检查范围见 [工作流配置](../.github/workflows/ci.yml)，其结果不能替代设备与外部服务验收。
