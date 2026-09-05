# 开发与验证

## 依赖安装

在 `server/` 建立独立 Python 虚拟环境，运行 `python -m pip install -e '.[dev]'`。`pytest-asyncio` 是开发依赖的一部分；只使用已装 pytest 的全局环境可能产生配置警告。

服务端依赖采用版本下限，暂未提供经过长期维护的跨平台锁文件。生产使用者应在验证后的环境中锁定实际版本，升级时执行回归。

## 服务端

```bash
cd server
python -m pytest -q
python -m compileall -q app
```

测试使用合成数据和临时目录，不需要真实模型、邮箱、小米账号、支付或推送凭据。修改外部集成后，单元测试不能替代实际服务验收。

从仓库根目录运行 `python scripts/smoke_local.py`，可验证已启动的本地 Echo 实例。该脚本验证 HTTP 注册、会话、同步/异步回复和幂等发送，拒绝非本机地址。

## Android

```bash
cd android
gradle :app:testStoreDebugUnitTest :app:assembleStoreDebug :app:assembleDirectDebug
```

改变 Manifest、签名或发行渠道时，再检查对应 Release 和合并后的 Manifest。布局、通知、位置、安装升级和支付宝 SDK 行为应根据改动进行模拟器或真机回归，并明确使用的是 Debug 还是正式签名包。

## 配置文档

`docs/CONFIGURATION_DEFAULTS.md` 是当前 `Settings` 默认值快照。增加或修改配置时同时更新说明；实际行为以 `server/app/config.py` 和环境覆盖为准，不要把生产 `.env` 复制进文档。

## 自动检查

仓库提供 GitHub Actions，执行服务端测试、源码文档检查及 Android Debug 构建。没有托管 Android 模拟器或真实第三方账号测试。工作流首次结果要等仓库创建、代码推送后才能确认。

本地 `python scripts/check_source.py` 检查 Git 跟踪文件中的敏感产物路径、明显 token 格式和文档链接。它不是完整安全审计，也不能发现所有格式的密钥或私密截图。

## 维护与发布

每次发布记录服务端 commit、Android versionName/versionCode、渠道、签名验证和测试范围。接口变更应与 Android 同一个 PR 更新。当前开源工作目录与作者旧的内部开发目录是独立快照；后续应将变更合并到本仓库，不要直接覆盖整个公开树或导入旧运维历史。
