# 人设形象与动态背景

本文说明 Auri 0.3.31 引入的「形象 + 动态聊天背景」能力：数据从哪来、如何选择变体、客户端如何渲染，以及公开仓库里的素材边界。

## 能力概述

- 同一个 Auri 品牌下提供 `female` / `male` 两套形象卡；形象会附带少量口癖、兴趣和 emoji 偏移，但不改写现有四个性格预设的核心气质。
- 每个形象有 12 张聊天背景变体，由服务端确定性选择，客户端只负责展示与开关。
- 用户可在账号中心关闭智能背景；聊天设置中「性别」选项会切换背景图库，同时刷新顶部角色头像。未开放账号、关闭开关或接口失败时，客户端保持原有渐变背景。
- 服务端会读取该用户最新的天气观测，匹配夏装、秋装叠穿、雨天撑伞和雪天场景；没有天气观测时退回原有的时间/情绪规则。

## 16 个变体

| variant | 时段 | 氛围 |
|---|---|---|
| `dawn_calm` | 清晨 | 平静、窗边逆光 |
| `day_focus` | 白天 | 专注、屏幕侧光 |
| `day_active` | 白天 | 活力、户外运动 |
| `day_gentle` | 白天 | 温柔、咖啡馆 |
| `dusk_warm` | 黄昏 | 温暖、晚霞风衣 |
| `dusk_lonely` | 黄昏 | 安静、城市初灯 |
| `night_cozy` | 深夜 | 亲密、台灯与沙发 |
| `night_quiet` | 深夜 | 安静、雨夜窗景 |
| `late_study` | 深夜 | 专注、深夜书桌 |
| `tired_rest` | 任意 | 疲惫、柔暗休息 |
| `sad_low` | 任意 | 低落、冷色雨窗 |
| `celebrate_up` | 任意 | 开心鼓励、明亮通透 |

## 天气与季节匹配

服务端优先使用最新 `weather` 观测（Open-Meteo 的 `weather_code` 与 `temperature_2m`），按以下顺序匹配：

1. `snow_winter`：降雪类天气码；
2. `rain_umbrella`：降雨/毛毛雨/雷雨类天气码；
3. `summer_light`：温度 ≥ 28℃ 或当地季节为夏；
4. `autumn_wind`：温度 ≤ 10℃ 或当地季节为秋；
5. 其他情况保持原有时段/情绪/关系阶段规则。

天气变体会覆盖普通时段背景，但会先让位于极端低落的 `sad_low` 和疲惫的 `tired_rest`。南半球自部署通过 `AURI_PORTRAIT_WEATHER_HEMISPHERE=south` 调整季节。没有天气观测、天气功能关闭或观测失败时，行为与旧版完全一致。

## 选择优先级

服务端只使用确定性信号，不为此新增模型调用：

1. 6 小时内有效低情绪关键词 -> `sad_low`
2. 昨夜睡眠分低于阈值且处于白天 -> `tired_rest`
3. 30 分钟内完成的运动记录 -> `day_active`
4. 6 小时内有效高情绪关键词 -> `celebrate_up`
5. 夜间且关系阶段为 familiar / close -> `night_cozy`（连续两次后与 `late_study`、`night_quiet` 轮换）
6. 其余按用户本地时段的默认变体：清晨 `dawn_calm`、白天 `day_gentle`、黄昏 `dusk_warm`、夜间 `night_quiet`

## 接口

- `GET /v1/portrait/current`：返回 `presentation`、`variant`、图片路径、时段、情绪、关系阶段、原因、过期秒数和是否启用。
- `GET /v1/persona/me`：在 `characters[]` 中返回 `avatar_url`，供聊天设置头像使用；服务端只在 `static/portrait/avatar/{presentation}.jpg` 存在时返回 URL，否则客户端回退渐变字母头像。
- `GET /v1/portrait/settings`：返回 `smart_background_enabled` 与该账号是否在灰度范围。
- `PUT /v1/portrait/settings`：用户开关。

未鉴权返回 401；未灰度或已关闭时 `enabled=false` 并返回安全兜底变体。

## 客户端行为

- Android 使用 Coil 2.6.0 加载 `image_url`；服务端返回相对路径时客户端按 API base URL 转成绝对地址。
- 冷启动先渲染 SharedPreferences 缓存，进入前台后按 `expires_in_seconds` 轮询；变体变化使用 400ms 交叉淡入。
- 图片上使用四段轻 Aurora 遮罩：顶部 62%、20% 处 22%、72% 处 16%、底部 64%，中下部人物更清楚，状态栏和输入区仍保留可读性。
- 智能背景开启时，助手气泡使用 78% 不透明度并加 7% 白色描边，时间戳增加深色底板；用户气泡保持不透明以保护主色白字对比度。
- 聊天设置页圆形头像来自 `avatar_url`，切换「性别」后立即更新；头像文件缺失或加载失败时回退渐变 +「Auri」字母头像。
- 账号中心「智能背景」开关关闭时立即回退渐变，并向 `PUT /v1/portrait/settings` 同步。

## 素材与提示词边界

真实成图由私有的 `tools/portrait-gen/` 调用 Qwen 图像模型生成，提示词由固定风格块、每角色 character block、negative prompt 和场景块拼接。角色一致性通过 identity 基准图参考 + 固定 seed 实现。

公开仓库只提供：

- 接口、数据模型、测试和本文规格；
- `server/app/static/portrait/` 下程序生成的渐变占位图（64 个文件，约 0.6 MB），以及 `server/app/static/portrait/avatar/{presentation}.jpg` 两张渐变占位头像。

公开仓库**不包含** Qwen 生成的真实角色成图、API Key、基准图与原始生成缓存。自部署若要启用真实背景，需要自己生成或准备图片，并按相同文件名放入 `server/app/static/portrait/{presentation}/{variant}.jpg` 与 `{variant}@540.jpg`，头像放入 `server/app/static/portrait/avatar/{presentation}.jpg`。

## 配置与灰度

- `AURI_PORTRAIT_ENABLED=false` 为公开默认值；启用后建议使用 `AURI_PORTRAIT_CANARY_USER_IDS` 先灰度。
- `AURI_PORTRAIT_DEFAULT_PRESENTATION` 默认 `female`。
- `AURI_PORTRAIT_MOOD_TTL_HOURS=6`、`AURI_PORTRAIT_REFRESH_SECONDS=900`、`AURI_PORTRAIT_SLEEP_TIRED_THRESHOLD=60`、`AURI_PORTRAIT_ACTIVE_WINDOW_MINUTES=30` 可调。
- `AURI_PORTRAIT_WEATHER_ENABLED=true`、`AURI_PORTRAIT_WEATHER_HEMISPHERE=north`、`AURI_PORTRAIT_WEATHER_HOT_THRESHOLD_C=28`、`AURI_PORTRAIT_WEATHER_COLD_THRESHOLD_C=10` 控制天气/季节匹配。

## 隐私说明

- 情绪信号来自用户消息中的关键词与关系状态，只在服务端本地 SQLite 中保存 6 小时，不调用额外模型。
- 背景图是按时间和状态选择的通用角色插画，不包含用户照片，也不用于推断或展示敏感信息。
- 用户关闭智能背景后不再返回图片；注销账号会同时清理形象状态与开关记录。
