# Auri Android

Kotlin + Jetpack Compose 客户端，包含聊天、日程、健康与账号页面。完整介绍见 [仓库首页](../README.md)，构建说明见 [Android 指南](../docs/ANDROID.md)。

需要 JDK 17、SDK 35、Gradle 8.9。首次构建需联网下载依赖。

```bash
gradle :app:testStoreDebugUnitTest :app:assembleStoreDebug
```

默认 Debug 包连接模拟器宿主机的 `http://10.0.2.2:8010/v1`，使用 `com.auri.community.debug` 包名。自建服务：`-PapiBaseUrl=https://your-domain.example/v1`。

无 JPush AppKey 时不初始化推送；无私有签名配置时 Release 为未签名产物。第三方 SDK 不因客户端采用 MIT 而改变其许可证。
