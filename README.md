# 🛰️ GitHub AI Coding Radar

自动扫描 GitHub 上的 AI coding 相关项目，按**多维质量与活性指标**打分排序，
每天生成一份 Markdown 报告。专门用来从海量新项目里捞出真正高质量、有生命力的工具。

## 它为什么比“看 Trending / 排 star”靠谱

普通的 star 排序有两个大坑，本工具都做了处理：

- **僵尸明星项目**：star 很高但早就没人维护。→ 用 `active_within_days` 过滤掉近期没 push 的。
- **无人认可的新项目** & **刷量爆款**：→ 综合看 star *增速*、commit 频率、贡献者数，而非 star 总量。

评分公式同时考虑：近 7 天 star 增速（权重最高）、近 30 天 commit 活跃度、
star 总量（取对数防碾压）、贡献者数、文档完整度。权重都在 `config.yaml` 里可调。

## 快速开始

1. **新建一个空仓库**（公开或私有都行），把本目录所有文件放进去。
2. `git push` 到 GitHub。
3. 到仓库的 **Settings → Actions → General**，确认 “Workflow permissions”
   设为 **Read and write**（这样 bot 才能把报告 commit 回来）。
4. 到 **Actions** 标签页，手动触发一次 `AI Coding Radar`（workflow_dispatch）验证。
5. 之后它会**每天自动跑**，报告生成在 `reports/latest.md`，历史存在 `reports/radar-YYYY-MM-DD.md`。

> 不需要额外配 token——Actions 自带的 `GITHUB_TOKEN` 对公开仓库搜索读取已足够。

## 本地试跑

```bash
pip install -r requirements.txt
export GITHUB_TOKEN=你的PAT          # 只需 public repo 读权限
python scripts/radar.py
cat reports/latest.md
```

## 调整行为：只改 config.yaml，不用动代码

| 想做什么 | 改哪里 |
|---|---|
| 扩大/缩小扫描领域 | `search.topics` / `search.keywords` |
| 放宽或收紧质量门槛 | `filters.*`（star 阈值、活跃天数、最少贡献者）|
| 改变“什么算高质量” | `scoring.*` 权重 |
| 改报告条数 | `report.top_n` |
| 改运行频率 | `.github/workflows/radar.yml` 里的 `cron` |

**几个常见调法举例：**
- 想覆盖前端/电商 AI：在 `search.topics` 加 `frontend` `ecommerce` `rag`。
- 觉得新项目漏太多：调低 `filters.min_stars_new`（如 50）、调大 `created_within_days`。
- 更看重“爆发性”：把 `scoring.star_velocity_7d` 权重调更高。

## 推到 Telegram / Slack / 邮件（可选进阶）

当前默认把报告 commit 回仓库。若想推送到别处，在 workflow 的
`Run radar` 之后加一步即可。例如 Telegram：

```yaml
      - name: Push to Telegram
        env:
          TG_TOKEN: ${{ secrets.TG_TOKEN }}
          TG_CHAT:  ${{ secrets.TG_CHAT }}
        run: |
          curl -s -X POST "https://api.telegram.org/bot$TG_TOKEN/sendMessage" \
            -d chat_id="$TG_CHAT" -d parse_mode=Markdown \
            --data-urlencode text@reports/latest.md
```
（Slack/邮件同理，换成对应的 webhook 即可。）

邮件发送使用 GitHub Secrets 中的 `MAIL_TO`，多个收件人用英文逗号分隔。

## 文件结构

```
.
├── config.yaml                    # ★ 所有可调参数都在这
├── requirements.txt
├── scripts/
│   └── radar.py                   # 核心：搜索 / 算指标 / 打分 / 出报告
├── .github/workflows/radar.yml    # 定时任务 + 自动提交
└── reports/                       # 自动生成
    ├── latest.md
    └── radar-YYYY-MM-DD.md
```
