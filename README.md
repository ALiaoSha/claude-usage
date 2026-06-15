# Claude Quota Logger

记录 Claude 订阅 **每个 5 小时窗口在 reset 之前用掉了多少配额**(真正的服务端"利用率百分比"历史),并生成离线 HTML 报告。顺带也记录 7 天周配额。

## 目的

Claude 订阅的限流是滚动的 5 小时窗口,窗口一旦 reset,服务端就不再告诉你"上个窗口最终用了多少"。本项目通过定时轮询 Anthropic 内部的用量接口,把每个窗口在 reset 前的利用率**沉淀成历史**,这样你能回看:

- 每个 5h 窗口收尾时用到了百分之几(`final`)
- 窗口期内的峰值(`peak`)
- 哪些窗口逼近或打满了配额(≥95%)
- 7 天周配额的走势

数据源是未公开接口 `GET https://api.anthropic.com/api/oauth/usage`,可能随时变化或失效。

## 工作原理

```
              每 10 分钟(launchd)
   usage API ──────────────────▶ claude_quota_logger.py ──▶ data/samples.csv  (原始样本, 唯一真相)
                                                                  │
                                                                  ▼
                                          render_report.py ──▶ data/report.html (按窗口派生 + 可视化)
```

- **logger 只追加原始样本**:每次轮询往 `data/samples.csv` 写一行 `(时间, 5h reset, 5h 利用率, 7d reset, 7d 利用率, opus, sonnet)`。
- **窗口由样本派生**:`render_report.py` 按 `resets_at` 分组,`final` = 窗口内最后一条样本,`peak` = 最大值。
- 这种"先存原始样本、再派生"的设计很稳:即使轮询恰好错过 reset 那一刻(笔记本休眠、漏跑),也能从已有样本恢复出该窗口的 final/peak,**不依赖抓到翻转瞬间**。

## 目录结构

所有东西都在本项目目录里,数据放在 `data/` 子目录:

```
claude_usage/
├── claude_quota_logger.py                 # 采样:轮询接口,追加到 data/samples.csv
├── render_report.py                       # 渲染:读样本,派生窗口,生成 data/report.html
├── com.liaozhou.claude-quota-logger.plist # macOS LaunchAgent,每 10 分钟自动跑上面两步
├── report_sample.html                     # 报告样例(参考用)
├── README.md
└── data/                                  # 运行时数据(自动生成)
    ├── samples.csv                         # 原始样本,唯一真相,删了历史就没了
    ├── report.html                         # 生成的离线报告,双击即可打开
    └── launchd.log                         # LaunchAgent 运行日志
```

> ⚠️ **路径不能放在 `~/Documents`、`~/Desktop`、`~/Downloads` 下**。macOS 的隐私保护(TCC)会禁止 launchd 等后台进程读取这些目录,定时任务会以 `Operation not permitted` 失败。本项目放在 `~/AI_workspace/claude_usage`(非保护路径)即可。

## 安装

前提:本机已登录 Claude Code(凭证会存在 macOS Keychain 的 `Claude Code-credentials` 项,logger 会自动从那里读取 OAuth token;也支持环境变量 `CLAUDE_CODE_OAUTH_TOKEN` 或 `~/.claude/.credentials.json`)。

```sh
cd ~/AI_workspace/claude_usage
cp com.liaozhou.claude-quota-logger.plist ~/Library/LaunchAgents/
launchctl load ~/Library/LaunchAgents/com.liaozhou.claude-quota-logger.plist
```

加载后会立刻跑一次。LaunchAgent 开机自启,休眠唤醒后会补跑漏掉的轮询,所以 reset 时刻能被覆盖到。

> 如果把项目挪到了别的路径,记得同步修改 `com.liaozhou.claude-quota-logger.plist` 里的三处绝对路径,再重新 `cp` + `launchctl load`。
>
> plist 里的 `EnvironmentVariables`(`HTTP_PROXY`/`HTTPS_PROXY`)是为了让 launchd 后台进程走本地代理访问接口——launchd 不继承你 shell 的代理变量。如果你直连即可访问,删掉这一段;如果用别的代理端口,改成你自己的。

## 使用

- **看报告**:打开 `data/report.html`(离线自包含,双击即可)。
  > 刚装好时"已完成窗口"是 0,属正常——等当前这个 5h 窗口 reset 之后,第一条历史才会出现。
- **手动跑一次**:
  ```sh
  python3 claude_quota_logger.py      # 采一个样本
  python3 render_report.py --open     # 重新生成报告并打开
  ```
- **持续前台轮询**(不依赖 launchd):
  ```sh
  python3 claude_quota_logger.py --loop
  ```

## 配置

- **轮询间隔**:`claude_quota_logger.py` 顶部的 `POLL_SECONDS`(默认 300 秒)。调小可让"reset 前最后读数"更贴近真实 reset 时刻。
- 注意:LaunchAgent 的触发间隔由 plist 里的 `StartInterval`(默认 600,即 10 分钟)控制,与 `POLL_SECONDS` 是两回事——单次运行模式下 logger 只采一个样本,真正的定时靠 launchd。改了间隔后重载 LaunchAgent 即可:
  ```sh
  launchctl unload ~/Library/LaunchAgents/com.liaozhou.claude-quota-logger.plist
  launchctl load   ~/Library/LaunchAgents/com.liaozhou.claude-quota-logger.plist
  ```

## 卸载

```sh
launchctl unload ~/Library/LaunchAgents/com.liaozhou.claude-quota-logger.plist
rm ~/Library/LaunchAgents/com.liaozhou.claude-quota-logger.plist
# 数据(data/)要不要留自己决定
```

## 维护提示

- `data/samples.csv` 每天约新增 288 行(5 分钟一条),一年约 10 万行,体积很小,一般无需清理。日后想精简可按月归档。
- 接口为未公开 API,若某天返回结构变化或鉴权失败,先在 Claude Code 里跑任意命令刷新登录,再看 `data/launchd.log` 排查。
