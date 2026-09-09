# Android 构建与分发

## 工具链

- JDK 17。
- Android SDK Platform 35；设备最低 Android 8.0 / API 26。
- Gradle 8.9、AGP 8.2.2、Kotlin 1.9.22；项目沿用已有工具链，升级应单独验证。
- Android Studio 打开 `android/`。本快照没有 Gradle Wrapper；安装 Gradle 8.9 并将命令加入 PATH，或指定本机 Gradle 发行目录。

在未提交的 `android/local.properties` 配置 `sdk.dir`，或设置 `ANDROID_HOME`。不要把本机绝对路径加入提交。

## 构建命令

```bash
gradle :app:testStoreDebugUnitTest :app:assembleStoreDebug
gradle :app:assembleDirectDebug
gradle :app:assembleStoreRelease :app:bundleStoreRelease
```

Windows 的发行包命令可用 `gradle.bat`。常见产物：

- `app/build/outputs/apk/store/debug/app-store-debug.apk`
- `app/build/outputs/apk/direct/debug/app-direct-debug.apk`
- `app/build/outputs/apk/store/release/` 下的 APK（无签名配置时文件名包含 unsigned）
- `app/build/outputs/bundle/storeRelease/app-store-release.aab`

## 隐私政策与自部署

0.3.29 在登录页和账号中心提供完整、可离线阅读的政策。内容源为 `android/app/src/main/assets/privacy-policy.json`，官网副本通过以下命令生成：

```bash
python scripts/render_privacy.py --document android/app/src/main/assets/privacy-policy.json --site server/app/static/site
```

从仓库根目录运行。自部署者必须将运营者、联系邮箱、实际服务商和保存机制改成自身情况，同时更新 `PrivacyConsent.POLICY_VERSION` 与 JSON 版本；旧政策的同意记录不能自动代表新版本同意。官网页面路径为 `/privacy`。默认未配置推送时，社区构建继续跳过 JPush 初始化。

## 渠道差异

| 差异 | store | direct |
|---|---|---|
| 应用内下载和安装更新 | 关闭 | 开启 |
| 特殊前台保活服务 | 关闭 | 配置推送后可开启 |
| 安装包权限 | 不包含应用内安装能力 | 按系统授权处理 |
| 更新方式 | 应用商店或手动替换 | 自建发布元数据与版本化 APK |

名字叫 store 不代表已通过任何商店审核，更不代表自动符合所有商店的支付或 AI 政策。

## 地址、包名和推送

```bash
gradle :app:assembleStoreDebug \
  -PapiBaseUrl=https://your-domain.example/v1 \
  -PapplicationId=org.example.auri \
  -PjpushAppKey=YOUR_JPUSH_APP_KEY
```

- 默认主包名 `com.auri.community`，Debug 默认追加 `.debug`；源代码 namespace 仍为 `com.auri.chat`。
- Debug 默认地址 `http://10.0.2.2:8010/v1`。Release 默认是不可用的示例域名，必须显式指定自己的地址。
- `jpushAppKey` 未设置时，JPush 初始化和保活启动关闭；App 内消息轮询仍工作。
- JPush Master Secret **只能在服务端**。自己的 JPush 应用应与最终 Android 包名、签名和服务器配置对应。
- 不提供通用的“杀进程后必达”承诺；各厂商通知通道需要单独接入、申请和测试。

## 自己的签名

用 `keytool` 或 Android Studio 创建你自己的 release keystore。不要向维护者索取官方签名。

创建被 gitignore 排除的 `android/keystore.properties`：

```properties
storeFile=/absolute/path/to/your-release.jks
storePassword=YOUR_STORE_PASSWORD
keyAlias=YOUR_KEY_ALIAS
keyPassword=YOUR_KEY_PASSWORD
```

无该文件时允许生成未签名 Release，以便检查构建。未签名产物不能当成正式可安装包。签名后用 SDK Build Tools 的 `apksigner verify --verbose` 验证 APK，再做目标真机安装与升级测试。

修改签名或包名会影响升级兼容、推送、支付、链接关联及商店记录。为 fork 使用独立应用名和包名，明确发布者身份。

## 直装更新

服务端按 SHA-256 保留不可变 APK 文件，客户端在下载/重试前刷新元数据，并同时验证大小与哈希。最终下载路径包含 `/v1/update/apk/{sha256}`；不能把相对业务路径错误拼到域名根目录。

自部署者需要发布自己的包、自己的递增 versionCode、自己的签名和变更记录。不要把官方 APK 上传到自建实例，期待其自动连接自建 API。

## UI 与权限

保留长对话、输入时机、分页缓存及账号/健康页面。新功能优先复用现有主题和 Material 组件。推送 SDK 在隐私同意后才初始化。公开运营前将现有隐私说明替换为本实例准确的政策、第三方 SDK 清单与运营者联系方式。

本地构建成功不代表实机通知、定位、邮件收件、支付回调和升级安装已通过，分别记录验证范围。
