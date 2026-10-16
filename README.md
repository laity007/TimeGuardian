# TimeGuardian 简体中文增强版

面向 Windows 的托盘校时工具，增加完整中文界面和代理出口时区自动跟随。

基于 [mixtrus/TimeGuardian](https://github.com/mixtrus/TimeGuardian) 开发，保留 MIT 许可证。原作者说明见 [README.upstream.md](README.upstream.md)，详细中文说明见 [README.zh-CN.md](README.zh-CN.md)。

## 主要改进

- **中文界面**：托盘菜单、设置、欢迎窗口、通知、错误提示、标准按钮和 Windows 时区名称。
- **自动跟随出口时区**：独立开关，默认每 60 秒检测出口，连续两轮稳定后切换。
- **多源核对**：对同一个出口 IP 查询 ipwho.is、ipinfo.io 和 ipapi.co，核对 IP、国家及至少两票的时区共识。
- **命名时区映射**：使用 Qt 的 IANA/Windows 映射，保留夏令时规则；不按固定 UTC 偏移猜测地区。
- **异常保护**：断网、无共识、出口抖动或无法映射时保留当前时区；关闭开关后，在途查询不能再应用。
- **源码运行支持**：无控制台窗口的登录自启动、单实例保护、运行状态和轮转日志。

“定期自动校时”调整系统时钟；“自动跟随代理出口时区”调整时区。两个开关独立，自动跟随不会更改 Windows 地区或区域格式。

后台校时不会请求管理员权限。当前进程没有校时权限时，只显示“权限不足”状态；需要校准系统时钟时，可使用 Windows 自动校时，或主动点击“立即校时”授权。取消授权后，后台不会再次提权。

## 安装与启动

已在 Windows 10 x64、Python 3.13 环境验证。以下命令在 PowerShell 中执行：

```powershell
git clone https://github.com/laity007/TimeGuardian.git
cd TimeGuardian
py -3.13 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\pythonw.exe .\launch.py
```

程序运行于任务栏右下角的通知区域，图标可能收在折叠菜单中。请通过 `launch.py` 启动，以启用单实例保护和启动错误记录。

第一次使用时，右键点击托盘图标，开启“自动跟随代理出口时区”。可在设置中调整检查间隔（30～3600 秒），勾选“登录 Windows 后自动启动”即可设置当前用户登录自启动。请保留虚拟环境和项目目录的位置。

“查看时区跟随状态…”显示检测结果和查询依据。“根据公网 IP 设置时区”可立即检查并应用可信结果；手动选择其他时区会暂停自动跟随。

上游下载的 EXE 可能需要配套的 `_internal` 目录，不能保证单个 EXE 可独立运行。这里推荐源码安装；本次改进没有发布新的 EXE 安装包。

## 识别规则与边界

程序先通过 Cloudflare trace 获取出口 IP 和国家，再查询该指定 IP；排除不匹配的记录后，要求至少两个不同服务对应同一 Windows 时区，并在有效结果中取得严格多数。应用前再次检测出口，自动模式还要求连续两轮结果稳定。

可信地理结果缓存 6 小时，但每轮仍检测出口。无共识时，对同一出口暂停地理查询 5 分钟后重试；手动检查可以立即重新查询。

IP 地理数据库可能不准确，多源一致不是物理位置证明。TUN 或代理分流可能造成多个出口；本功能跟随 Cloudflare trace 检测到的出口，不能让单一 Windows 时区同时匹配多个国家。Windows 自带的“自动设置时区”如果开启，可能与本功能竞争，应避免同时启用两套自动时区管理。

## 配置和日志

| 内容 | 路径 |
|---|---|
| 用户设置 | `%APPDATA%\TimeGuardian\config.json` |
| 最近检测状态 | `%LOCALAPPDATA%\TimeGuardian\timezone-follow-status.json` |
| 跟随日志（UTC，轮转保留） | `%LOCALAPPDATA%\TimeGuardian\timezone-follow.log` |
| 启动错误 | `%LOCALAPPDATA%\TimeGuardian\startup-error.log` |

运行配置、日志和虚拟环境不随 Git 提交。

## 验证

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
```

30 项测试覆盖时区映射、冬夏令时、查询源冲突、出口改变、缓存、连续确认、关闭后丢弃在途结果，以及后台禁止提权、授权取消、校时结果和 NTP 时间戳解析。测试模拟网络和时区写入，不修改真实系统时区；映射和权限测试需要 Windows。

另已在 Windows 10 实机验证：程序经过两轮自动检测，将系统从北京时间切换为洛杉矶时区，并回读确认成功。其他代理节点的迁移和抖动场景以模拟测试验证。
