#!/usr/bin/env python3
"""
GitHub AI Coding Radar
======================
定时扫描 GitHub 上的 AI coding 相关项目，按多维“质量/活性”指标打分排序，
生成一份 Markdown 报告。

与“只看 star 总数”不同，本脚本重点关注：
  - star 增长速度（近 7 天）—— 真实热度的最强信号
  - 近期 commit 频率 —— 项目是否还活着
  - 贡献者数量 —— 社区健康度，单人项目风险高
  - 文档完整度
并据此过滤掉“高 star 但已停更的僵尸项目”和“无人认可的新项目”。

用法:
  python scripts/radar.py                 # 用 config.yaml
  python scripts/radar.py --config x.yaml # 指定配置
环境变量:
  GITHUB_TOKEN  必填。GitHub PAT（classic 或 fine-grained，只需 public repo 读权限）
"""

import os
import sys
import math
import time
import json
import argparse
import datetime as dt
from pathlib import Path

import requests
import yaml

API = "https://api.github.com"
UTC = dt.timezone.utc


# ----------------------------------------------------------------------
# HTTP 辅助：带重试 + 速率限制处理
# ----------------------------------------------------------------------
class GH:
    def __init__(self, token):
        self.s = requests.Session()
        self.s.headers.update({
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "gh-ai-radar",
        })

    def get(self, url, params=None, max_retries=4):
        for attempt in range(max_retries):
            r = self.s.get(url, params=params, timeout=30)
            # 速率限制：等到重置再试
            if r.status_code == 403 and "rate limit" in r.text.lower():
                reset = int(r.headers.get("X-RateLimit-Reset", time.time() + 60))
                wait = max(reset - int(time.time()), 1) + 1
                print(f"  [rate-limit] 等待 {wait}s ...", file=sys.stderr)
                time.sleep(min(wait, 120))
                continue
            if r.status_code in (502, 503, 504):
                time.sleep(2 ** attempt)
                continue
            r.raise_for_status()
            return r
        r.raise_for_status()
        return r


# ----------------------------------------------------------------------
# 搜索：组合 topic + keyword 查询，收集候选仓库
# ----------------------------------------------------------------------
def build_queries(cfg):
    f = cfg["filters"]
    created_after = (dt.datetime.now(UTC)
                     - dt.timedelta(days=f["created_within_days"])).date().isoformat()
    pushed_after = (dt.datetime.now(UTC)
                    - dt.timedelta(days=f["active_within_days"])).date().isoformat()

    queries = []
    # 1) 新星：近期创建 + 已有一定 star + 仍活跃
    for topic in cfg["search"]["topics"]:
        queries.append(
            f'topic:{topic} created:>{created_after} '
            f'stars:>={f["min_stars_new"]} pushed:>{pushed_after}'
        )
    # 2) 成熟活跃项目：高 star + 近期仍 push（抓住持续演进的）
    for topic in cfg["search"]["topics"]:
        queries.append(
            f'topic:{topic} stars:>={f["min_stars_established"]} '
            f'pushed:>{pushed_after}'
        )
    # 3) 关键词补充（覆盖没打 topic 的项目）
    for kw in cfg["search"]["keywords"]:
        queries.append(
            f'"{kw}" in:name,description created:>{created_after} '
            f'stars:>={f["min_stars_new"]} pushed:>{pushed_after}'
        )
    return queries


def search_repos(gh, cfg):
    seen = {}
    for q in build_queries(cfg):
        try:
            r = gh.get(f"{API}/search/repositories",
                       params={"q": q, "sort": "stars", "order": "desc",
                               "per_page": 50})
        except requests.HTTPError as e:
            print(f"  [warn] 查询失败已跳过: {q[:60]}... ({e})", file=sys.stderr)
            continue
        for item in r.json().get("items", []):
            seen.setdefault(item["full_name"], item)
        if len(seen) >= cfg["runtime"]["max_candidates"]:
            break
        time.sleep(1)  # 礼貌一点，避免触发二级速率限制
    return list(seen.values())


# ----------------------------------------------------------------------
# 活性指标：star 增速、commit 频率、贡献者数
# ----------------------------------------------------------------------
def days_ago(iso_str):
    if not iso_str:
        return 9999
    t = dt.datetime.fromisoformat(iso_str.replace("Z", "+00:00"))
    return (dt.datetime.now(UTC) - t).days


def get_metrics(gh, repo, cfg):
    """拉取单个仓库的详细活性指标。"""
    full = repo["full_name"]
    m = {
        "star_velocity_7d": 0.0,
        "commits_30d": 0,
        "contributors": 1,
        "has_docs": False,
    }

    # 贡献者数（用 anon + per_page=1 读 Link 头里的总数，省流量）
    try:
        r = gh.get(f"{API}/repos/{full}/contributors",
                   params={"anon": "1", "per_page": 1})
        link = r.headers.get("Link", "")
        if 'rel="last"' in link:
            import re
            mlast = re.search(r'[?&]page=(\d+)>; rel="last"', link)
            if mlast:
                m["contributors"] = int(mlast.group(1))
        elif r.json():
            m["contributors"] = len(r.json())
    except requests.HTTPError:
        pass

    # 近 30 天 commit 数
    since = (dt.datetime.now(UTC) - dt.timedelta(days=30)).isoformat()
    try:
        r = gh.get(f"{API}/repos/{full}/commits",
                   params={"since": since, "per_page": 100})
        m["commits_30d"] = len(r.json()) if isinstance(r.json(), list) else 0
    except requests.HTTPError:
        pass

    # star 增速：用 stargazers 时间戳估算近 7 天新增
    # （需要 star-version 媒体类型；只取最后几页近似）
    try:
        r = gh.s.get(
            f"{API}/repos/{full}/stargazers",
            headers={"Accept": "application/vnd.github.star+json"},
            params={"per_page": 100, "page": 1},
            timeout=30,
        )
        # 直接跳到最后一页拿最近的 star 时间
        link = r.headers.get("Link", "")
        last_page = 1
        if 'rel="last"' in link:
            import re
            mlast = re.search(r'[?&]page=(\d+)>; rel="last"', link)
            if mlast:
                last_page = int(mlast.group(1))
        rr = gh.s.get(
            f"{API}/repos/{full}/stargazers",
            headers={"Accept": "application/vnd.github.star+json"},
            params={"per_page": 100, "page": last_page},
            timeout=30,
        )
        recent = 0
        for sg in (rr.json() if isinstance(rr.json(), list) else []):
            ts = sg.get("starred_at")
            if ts and days_ago(ts) <= 7:
                recent += 1
        m["star_velocity_7d"] = float(recent)
    except (requests.HTTPError, requests.RequestException):
        pass

    # 文档：有 README + description 即认为基本达标
    m["has_docs"] = bool(repo.get("description")) and repo.get("size", 0) > 0

    return m


# ----------------------------------------------------------------------
# 打分
# ----------------------------------------------------------------------
def score(repo, m, cfg):
    w = cfg["scoring"]
    s = 0.0
    s += w["star_velocity_7d"] * m["star_velocity_7d"]
    s += w["recent_commit_freq"] * min(m["commits_30d"], 50)  # 截顶，避免刷量
    s += w["total_stars_log"] * math.log10(max(repo["stargazers_count"], 1))
    s += w["contributors"] * min(m["contributors"], 30)
    s += w["has_readme_docs"] * (1.0 if m["has_docs"] else 0.0)
    return round(s, 2)


def passes_filters(repo, m, cfg):
    f = cfg["filters"]
    if days_ago(repo.get("pushed_at")) > f["active_within_days"]:
        return False
    if m["contributors"] < f["min_contributors"]:
        return False
    return True


# ----------------------------------------------------------------------
# 已发送项目状态：避免重复发送同一个仓库
# ----------------------------------------------------------------------
def sent_state_path(cfg):
    filename = cfg["report"].get("sent_state_file", "sent-projects.json")
    return Path(cfg["report"]["output_dir"]) / filename


def load_sent_projects(path):
    path = Path(path)
    if not path.exists():
        return set()
    data = json.loads(path.read_text(encoding="utf-8"))
    return {str(item) for item in data}


def save_sent_projects(path, projects):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(sorted(projects), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def filter_unsent_ranked(ranked, sent_projects):
    return [
        (repo, metrics, sc)
        for repo, metrics, sc in ranked
        if repo["full_name"] not in sent_projects
    ]


# ----------------------------------------------------------------------
# 报告生成
# ----------------------------------------------------------------------
def make_report(ranked, cfg, evaluated_count=None, skipped_sent_count=0):
    now = dt.datetime.now(UTC).strftime("%Y-%m-%d %H:%M UTC")
    evaluated = len(ranked) if evaluated_count is None else evaluated_count
    lines = [
        f"# 🛰️ GitHub AI Coding Radar — {now}",
        "",
        f"扫描范围: `{', '.join(cfg['search']['topics'])}`",
        f"共评估候选 {evaluated} 个，已过滤历史发送项目 {skipped_sent_count} 个。",
        "",
        "评分综合考虑：近 7 天 star 增速、近 30 天 commit 活跃度、"
        "star 总量、贡献者数、文档完整度。",
        "",
    ]
    if not ranked:
        lines += [
            "本次没有发现新的合适项目；历史已发送项目不会重复出现在报告中。",
        ]
    else:
        lines += [
            f"下列为本次新增项目综合评分 Top {min(cfg['report']['top_n'], len(ranked))}。",
            "",
            "| # | 项目 | ⭐ | 7d↑ | commits/30d | 贡献者 | 评分 | 简介 |",
            "|---|------|----|----|----|----|----|------|",
        ]
    for i, (repo, m, sc) in enumerate(ranked[:cfg["report"]["top_n"]], 1):
        desc = (repo.get("description") or "").replace("|", "\\|")[:90]
        lines.append(
            f"| {i} | [{repo['full_name']}]({repo['html_url']}) "
            f"| {repo['stargazers_count']} "
            f"| {int(m['star_velocity_7d'])} "
            f"| {m['commits_30d']} "
            f"| {m['contributors']} "
            f"| {sc} "
            f"| {desc} |"
        )
    lines += [
        "",
        "---",
        "*由 gh-ai-radar 自动生成。调整 `config.yaml` 中的 topics / 阈值 / 权重可改变结果。*",
    ]
    return "\n".join(lines)


def write_report(content, cfg, ranked=None):
    out = Path(cfg["report"]["output_dir"])
    out.mkdir(parents=True, exist_ok=True)
    stamp = dt.datetime.now(UTC).strftime("%Y-%m-%d")
    if cfg["report"]["keep_history"]:
        (out / f"radar-{stamp}.md").write_text(content, encoding="utf-8")
    (out / "latest.md").write_text(content, encoding="utf-8")
    if ranked is not None:
        state_path = sent_state_path(cfg)
        sent = load_sent_projects(state_path)
        sent.update(repo["full_name"] for repo, _, _ in ranked[:cfg["report"]["top_n"]])
        save_sent_projects(state_path, sent)
    print(f"  报告已写入 {out}/latest.md")


# ----------------------------------------------------------------------
# 主流程
# ----------------------------------------------------------------------
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", default="config.yaml")
    args = ap.parse_args()

    token = os.environ.get("GITHUB_TOKEN")
    if not token:
        print("错误: 未设置环境变量 GITHUB_TOKEN", file=sys.stderr)
        sys.exit(1)

    cfg = yaml.safe_load(Path(args.config).read_text(encoding="utf-8"))
    gh = GH(token)

    print("→ 搜索候选仓库 ...")
    candidates = search_repos(gh, cfg)
    print(f"  收集到 {len(candidates)} 个去重候选")

    print("→ 计算活性指标并打分 ...")
    ranked = []
    for idx, repo in enumerate(candidates, 1):
        if cfg["runtime"]["fetch_detailed_metrics"]:
            m = get_metrics(gh, repo, cfg)
        else:
            m = {"star_velocity_7d": 0, "commits_30d": 0,
                 "contributors": cfg["filters"]["min_contributors"],
                 "has_docs": bool(repo.get("description"))}
        if not passes_filters(repo, m, cfg):
            continue
        ranked.append((repo, m, score(repo, m, cfg)))
        if idx % 20 == 0:
            print(f"  已处理 {idx}/{len(candidates)}")

    ranked.sort(key=lambda x: x[2], reverse=True)
    print(f"  通过过滤 {len(ranked)} 个")

    sent_projects = load_sent_projects(sent_state_path(cfg))
    unsent_ranked = filter_unsent_ranked(ranked, sent_projects)
    skipped_sent_count = len(ranked) - len(unsent_ranked)
    if skipped_sent_count:
        print(f"  已跳过历史发送项目 {skipped_sent_count} 个")

    print("→ 生成报告 ...")
    report = make_report(
        unsent_ranked,
        cfg,
        evaluated_count=len(ranked),
        skipped_sent_count=skipped_sent_count,
    )
    write_report(report, cfg, unsent_ranked)
    print("✓ 完成")


if __name__ == "__main__":
    main()
