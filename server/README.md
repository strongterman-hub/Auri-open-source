# Auri Server

完整项目介绍见 [仓库首页](../README.md)。本目录包含 FastAPI 服务、Agent 工具循环、聊天队列、可配置人设与关系行为、智能背景、天气/季节匹配与角色头像、记忆、主动消息、日程、健康、账号、Credits、后台和官网源码。

- Python 3.12 推荐；安装：`python -m pip install -e '.[dev]'`
- 配置：复制 `.env.example` 为 `.env`，不要提交真实配置。
- 启动：`python -m uvicorn app.main:app --host 127.0.0.1 --port 8010`
- 测试：`python -m pytest -q`
- API：启动后访问 `/docs`；健康检查 `/v1/health`。

详见 [快速开始](../docs/GETTING_STARTED.md)、[配置](../docs/CONFIGURATION.md)、[部署](../docs/DEPLOYMENT.md) 和 [架构](../docs/ARCHITECTURE.md)。默认 Echo 模式不产生真实 AI 回答。以本目录作为工作目录运行，数据写入本目录的 `data/`。
