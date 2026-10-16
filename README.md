# VRC 好友

AstrBot 插件：查看 VRChat 好友在线状态和房间，收藏优先展示，订阅上线提醒。每个 QQ 独立保存账号与设置，默认白底毛玻璃卡片。

## 安装

需要 Python 3.10+、AstrBot 4.x，使用 NapCat / aiocqhttp 或 Satori 等支持主动消息的适配器。

1. 在 AstrBot 插件页面选择从仓库安装，填写 `https://github.com/muyuawa/astrbot_plugin_vrcfriends`；也可上传安装包，或将插件文件夹放入 `data/plugins/` 后重载。
2. Python 依赖会自动检查并通过 AstrBot 安装。缺少 Chromium 时，插件会在后台自动下载；首次准备进度可查看 AstrBot 日志。
3. 在“机器人管理者联系方式”中填写 `contact_url`，然后重载插件。最简单是填 `mailto:你的真实邮箱`：例如邮箱是 `123456@qq.com`，就填 `mailto:123456@qq.com`；也可填自己的 HTTPS 联系页面。

此项由管理员填写一次，作为请求的应用联系方式交给 VRChat。插件不会发邮件，也不需要邮箱密码；各 QQ 用户仍分别登录自己的 VRC 账号。

可用 `browser_path` 指定已有 Chrome / Edge，跳过浏览器下载。自动安装需要联网；网络、权限或 Linux 系统组件不足导致准备失败时，图片会改用文字，修复后重载可重试。精简 Linux 镜像可能需补齐浏览器系统库和中文字体。

### 手动安装（备用）

使用运行 AstrBot 的同一用户和 Python 环境，在 AstrBot 根目录执行。使用虚拟环境时先激活；Docker 部署需在 AstrBot 容器内执行。`python` 可替换为实际使用的解释器路径：

```bash
python -m pip install -r data/plugins/astrbot_plugin_vrcfriends/requirements.txt
python -m playwright install chromium
```

第一条安装 Python 依赖，第二条安装图片渲染用的浏览器；已配置可用 `browser_path` 时可跳过第二条。若插件目录名称不同，请相应修改路径。

Debian / Ubuntu 缺少浏览器系统库时，可按 [Playwright 官方说明](https://playwright.dev/python/docs/browsers#install-system-dependencies) 补装（需要系统管理员权限）：

```bash
python -m playwright install-deps chromium
```

安装完成后重载插件。

## 登录

每个 QQ 分别私聊机器人：

```text
/vrc 登录 用户名 密码
```

含空格的参数用英文引号包住。按提示完成验证，以下方式择一：

```text
/vrc 验证 123456
/vrc 邮箱验证 123456
/vrc 恢复码 你的恢复码
```

收到新地点登录确认邮件（`please verify your login`）时，确认是自己的操作后点击邮件中的确认链接，再重新发送 `/vrc 登录 用户名 密码`；之后若提示邮箱验证码，再按原提示验证。接口未明确原因的登录失败会提示检查此类邮件。

也可私聊 `/vrc Cookie authCookie的值 [twoFactorAuthCookie的值]`。邮箱激活或网页人机验证需先在 [VRChat 官网](https://vrchat.com/home/login) 或官方邮件中完成，再重试。

## 指令

| 指令 | 作用 |
| --- | --- |
| `/vrc 好友 [页码/昵称/用户ID]` | 查看在线好友或指定好友 |
| `/vrc 好友 全部 [页码]` | 包含网页在线和离线好友 |
| `/vrc 查找 关键字 [页码]` | 在自己的全部好友中搜索，支持部分昵称和用户 ID，包含离线好友 |
| `/vrc 收藏 昵称`、`/vrc 取消收藏 昵称` | 设置或取消优先展示 |
| `/vrc 收藏 [页码]` | 查看收藏列表 |
| `/vrc 订阅 昵称`、`/vrc 取消订阅 昵称` | 开启或关闭当前会话的上线提醒 |
| `/vrc 订阅 [页码]` | 查看当前会话订阅 |
| `/vrc 每页 10` | 每张图人数，范围 1–30，默认 8；超出分页 |
| 图片附 `/vrc 背景` | 设置背景，也支持回复图片使用 |
| `/vrc 背景 默认` | 恢复白底 |
| `/vrc 账号`、`/vrc 退出` | 查看连接或退出登录 |
| `/vrc 帮助` | 查看完整帮助 |

先 `/vrc 查找 澪`，再用 `/vrc 收藏 #01` 或 `/vrc 订阅 #01`。查找时含空格或以数字结尾的昵称可用英文引号包住，例如 `/vrc 查找 "Name 123"`。

昵称可换成最近列表的 `#编号`，在当前会话有效 10 分钟；同名或纯数字昵称使用用户 ID。

## 说明

- 私人或隐藏位置显示“私人房间 · 位置未公开”。收藏与订阅独立，收藏不会自动开启提醒。
- 名字左侧显示好友头像，右侧在地图封面可获取时显示封面；头像不可用时显示昵称首字，私人房间不显示地图封面。
- 默认每 120 秒检查一次，可用 `poll_interval` 设置为 60–3600 秒。网页上线、换房间、换灯色不提醒；首次同步及断线恢复不补发已在线好友。
- 仅使用只读 HTTP 轮询，无 WebSocket 或在线心跳；VRChat 仍可能将 API 会话标记为网页活跃。
- 认证 Cookie 按 QQ 独立加密，密码和验证码不落盘。数据位于 `data/plugin_data/astrbot_plugin_vrcfriends/`，备份须同时保留数据库和 `session.key`。
- 已通过本地测试，真实 VRChat 登录和 QQ 收发需在你的实例中验证。
