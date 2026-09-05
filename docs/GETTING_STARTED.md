# 从零运行 Auri

## 1. 环境

服务端推荐 Python 3.12；Android 需要 JDK 17、Android SDK Platform 35、Gradle 8.9。两端可分开运行。仓库不包含 SDK、Python 虚拟环境或官方签名。

## 2. 启动服务端

Linux/macOS：

```bash
git clone https://github.com/strongterman-hub/Auri-open-source.git
cd Auri-open-source/server
python3.12 -m venv .venv
source .venv/bin/activate
python -m pip install -e '.[dev]'
cp .env.example .env
python -m uvicorn app.main:app --host 127.0.0.1 --port 8010
```

Windows PowerShell：

```powershell
git clone https://github.com/strongterman-hub/Auri-open-source.git
Set-Location Auri-open-source/server
py -3.12 -m venv .venv
& .\.venv\Scripts\python.exe -m pip install -e '.[dev]'
Copy-Item .env.example .env
& .\.venv\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8010
```

上述命令使用本地 Echo 模式。检查 `/v1/health` 和 `/docs`。首次运行会创建本实例自己的 `data/`，没有内置用户，也不会连接官方账号数据。

## 3. 先验证 API 闭环

保持服务器运行，在另一个终端从仓库根目录执行：

```bash
python scripts/smoke_local.py
```

脚本只允许默认的本机地址，创建随机 `example.com` 测试账号，创建会话、发送消息并读取历史；不会调用官方服务。注册验证码关闭是本地测试前提。测试账号会保留在本地数据目录，日志不输出 token。

也可以在 `/docs` 按顺序调用：

1. `POST /v1/auth/register`，JSON 包含 `email`、`password`。
2. 取响应中的 token，用 `Authorization: Bearer ...` 调用受保护接口。
3. `POST /v1/sessions/ensure`，传入 `{"user_id":"注册时的邮箱"}`；服务端仍以鉴权账号为准。
4. `POST /v1/sessions/{id}/messages` 测试同步 Echo 回复。
5. Android 实际使用 `/messages/async` 和 `/updates?after=...`；不要把“消息已接收”当成“模型已回复”。

## 4. 接入真实模型

编辑 `.env`：

```dotenv
AURI_LLM_PROVIDER=openai-compatible
AURI_LLM_BASE_URL=https://your-model-provider.example/v1
AURI_LLM_API_KEY=replace-with-your-own-key
AURI_LLM_MODEL=your-tool-capable-model
# 只有提供商确实支持时才设置对应视觉模型
AURI_LLM_VISION_MODEL=your-vision-model
```

重启服务。模型名称应取自你实际供应商的可用列表，不要假定生产记录里的某个实验模型对所有账号开放。模型适配使用 Chat Completions 风格的流式工具调用；没有覆盖所有“兼容 API”的差异。

`core/pricing.py` 的计价表是当前项目实现，不会自动读取供应商最新价格。换模型或启用收费前，校验输入、缓存、输出和汇率口径。

## 5. 连接 Android

默认 `storeDebug` 使用官方 Android 模拟器地址 `http://10.0.2.2:8010/v1`。开发环境允许 HTTP，正式环境应使用 HTTPS。

```bash
cd android
gradle :app:assembleStoreDebug
adb devices -l
adb -s YOUR_DEVICE_SERIAL install -r app/build/outputs/apk/store/debug/app-store-debug.apk
```

真机 USB 调试可以：

```bash
adb -s YOUR_DEVICE_SERIAL reverse tcp:8010 tcp:8010
gradle :app:assembleStoreDebug -PapiBaseUrl=http://127.0.0.1:8010/v1
```

检查设备序列号后再安装，避免误装到其他手机。Android 登录/注册界面包含邮箱验证码流程；无邮件服务时，先通过上面的本地脚本或 API 注册账号，再在 App 登录。完整验证码体验需要 Resend；这不是本地 Echo 测试的必要条件。

## 6. 再逐项开启外部能力

先完成基本聊天，再参照 [配置参考](CONFIGURATION.md) 配置推送、邮件、健康和主动消息。第一次调试不要同时启用收费、主动轮询和所有外部服务，否则错误来源和调用费用难以判断。

### 常见问题

| 现象 | 首先检查 |
|---|---|
| 回答只是回显 | `.env` 的 provider 是否仍为 echo，模型 Key 是否设置 |
| Android 网络失败 | API 地址是否含 `/v1`；真机 localhost 是手机本身；防火墙与 adb reverse |
| 前台收到、后台没通知 | JPush 是否配置；用户是否同意隐私并允许通知；设备省电与厂商通道 |
| 验证码不可用 | 自己的 Resend 域名、Key、发件地址和持久验证码密钥 |
| 健康没有数据 | 小米是否绑定、云端是否有数据、设备/地区/协议是否支持 |
| 主动消息没有出现 | 服务端开关、用户开关、余额、免打扰、待回复状态与来源可用性 |
| 官网不能下载 APK | 本实例尚未配置发布元数据和安装包；这是独立自部署环境 |
| 构建提示找不到 SDK | Android Studio SDK 配置或未提交的 `local.properties` 中设置 `sdk.dir` |
| Release 未签名 | 为你自己的包配置 `keystore.properties`，官方签名不会提供 |
