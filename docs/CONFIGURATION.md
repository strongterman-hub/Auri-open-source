# 配置参考

服务端从进程环境和工作目录的 `.env` 读取配置，前缀统一为 `AURI_`。实现以 [`Settings`](../server/app/config.py) 为准。以下按用途列出常用项，完整默认值索引见 [CONFIGURATION_DEFAULTS.md](CONFIGURATION_DEFAULTS.md)。默认值不是推荐的公众运营配置。

## 模型与本地开发

| 配置 | 作用 |
|---|---|
| `AURI_ENVIRONMENT` | development / production 环境标签 |
| `AURI_DATA_DIR` | 持久数据根目录，默认 `data` |
| `AURI_LLM_PROVIDER` | `echo` 本地回显，或 `openai-compatible` |
| `AURI_LLM_BASE_URL` | 模型 API 基址，一般包含 `/v1` |
| `AURI_LLM_API_KEY` | 自己的模型密钥，只放私有环境 |
| `AURI_LLM_MODEL` | 供应商实际可用且支持工具调用的模型 |
| `AURI_LLM_VISION_MODEL` | 图片请求模型，需要实际支持图片 |
| `AURI_DEFAULT_TIMEZONE` | 未上报时区时的默认值 |
| `AURI_CORS_ORIGINS` | JSON 数组，例如 `["https://your-domain.example"]` |

默认 `.env.example` 强制 Echo；单独使用代码默认值时，没有模型 Key 也回退 Echo。真实 AI 测试应同时验证模型名称、流式输出、工具调用和超时行为。

## 账号与管理后台

设置 `AURI_ADMIN_USERNAME` 和独立的强随机 `AURI_ADMIN_PASSWORD` 后，访问 `/admin/login`。不配置凭据时后台不可用。`AURI_DEBUG_UI_ENABLED` 默认 false；不要因方便调试而长期公开调试入口。

Resend 邮件需要：

```dotenv
AURI_RESEND_API_KEY=YOUR_RESEND_KEY
AURI_AUTH_EMAIL_FROM=Auri <no-reply@your-domain.example>
AURI_AUTH_CODE_SECRET=YOUR_PERSISTENT_RANDOM_SECRET
AURI_AUTH_EMAIL_VERIFICATION_REQUIRED=true
```

发件域必须在你自己的服务商账号验证。验证码默认 600 秒有效、60 秒重发间隔，并限制错误次数与发送频率。验证码密钥需持久保存，不能每次重启重新生成。

开发时关闭注册验证码，可以通过 API 创建测试账号；Android 注册/找回/改密的完整邮件流程仍需邮件服务。认证成功返回的 Bearer token 只保存在客户端，服务端保存摘要。

## 主动联系与异步回复

`AURI_CHAT_REPLY_ENABLED=true` 启动持久回复队列，默认 tick 为 0.5 秒。fast/normal/away 的范围和重试参数可配置；这些是规划范围，不是端到端延迟承诺。模型生成时间和网络时间仍会叠加。

`AURI_PROACTIVE_ENABLED=true` 打开全局主动调度，用户仍需要开启自己的主动消息设置。没有真实模型、可靠时间/情境和预算前，不建议开放无约束的后台调用。

以下组控制节奏：`AURI_PROACTIVE_PACING_*`、`AURI_PROACTIVE_ONBOARDING_*`、`AURI_PROACTIVE_CONTEXT_*`。建议先保留默认值，根据 gate、context、reply 审计解释具体行为，再调整参数。

主动决策即使最终保持静默也可能消耗模型 token；事件提取和摘要也有成本。

## JPush

服务端：

```dotenv
AURI_JPUSH_APP_KEY=YOUR_JPUSH_APP_KEY
AURI_JPUSH_MASTER_SECRET=YOUR_JPUSH_MASTER_SECRET
```

Android 构建传入同一应用的 `-PjpushAppKey=...`。Master Secret 不能进入 Android。未配置推送时使用空发送器，服务端会话落库与前台轮询不受影响。通知需要用户隐私同意、系统授权和设备可用通道。

## 小米健康

`AURI_XIAOMI_SECRET_KEY` 使用 Fernet 格式。生成一次并安全保存：

```bash
python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```

终端会显示生成的密钥，不要将输出粘贴到 Issue 或提交记录中。修改/丢失密钥可能导致已保存的小米凭据无法解密。用户应通过自己实例的扫码流程绑定，不共享官方账号或导出的登录态。

`AURI_HEALTH_SYNC_ENABLED=true` 启用后台同步。`AURI_SLEEP_DUAL_SCORE_ENABLED` 控制计算，`AURI_SLEEP_DUAL_SCORE_VISIBLE` 控制展示；后者默认 false。小米接口是实验性社区适配，代码授权不能替代平台接口授权。

## 搜索、天气、提醒

- 搜索：`AURI_WEB_SEARCH_PROVIDER=none|openwebsearch|duckduckgo|tavily`。openwebsearch 需要单独运行与现有协议匹配的服务；Tavily 需要 `AURI_TAVILY_API_KEY`。
- 网页读取有网络、大小及解析限制；第三方网页内容不能作为高优先级指令。
- 天气后台：`AURI_WEATHER_ENABLED`、`AURI_WEATHER_LATITUDE`、`AURI_WEATHER_LONGITUDE`。固定坐标是部署者回退位置，不是用户 GPS。
- 提醒：`AURI_REMINDER_ENABLED` 默认 true，时区由 `AURI_REMINDER_TIMEZONE` 等配置控制。
- 热点：`AURI_TRENDING_ENABLED` 默认 false，启用前先验证搜索来源。

## Credits 与支付宝

本地默认 `AURI_BILLING_ENFORCEMENT_ENABLED=false`、`AURI_ALIPAY_ENABLED=false`。模型调用费用仍由供应商向你的账号收取，关闭内部扣费不是免费模型服务。

支付宝配置包括自己的 `AURI_ALIPAY_APP_ID`、`AURI_ALIPAY_PRIVATE_KEY_FILE`、`AURI_ALIPAY_PUBLIC_KEY_FILE`、`AURI_ALIPAY_GATEWAY` 与 `AURI_ALIPAY_NOTIFY_URL`。Key 文件置于私有目录，赋予服务账号最小读取权限。不要把密钥写进镜像。

默认网关为沙箱，Android Debug 也使用沙箱；Release 使用正式支付模式。支付开启前统一两端环境，验证创建订单、真实/沙箱付款、RSA2 回调验签、金额、订单归属、主动查单和重复通知幂等。先验证再开启强制扣费。

`core/pricing.py` 维护模型计价；切换供应商、模型或收费方案时重新核对。任意未知模型目前会映射到项目内默认计价分类，不能据此声称供应商实际成本已准确核算。
