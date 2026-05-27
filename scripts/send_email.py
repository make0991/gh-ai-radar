#!/usr/bin/env python3
"""
把 reports/latest.md 通过 Gmail SMTP 发送到指定邮箱。
依赖环境变量（在 GitHub Actions Secrets 里配置）:
  GMAIL_USER      你的 Gmail 地址（用来登录 SMTP 的发件账号）
  GMAIL_APP_PASS  Gmail 应用专用密码（16 位，不是登录密码！）
  MAIL_TO         收件人地址
"""
import os
import sys
import smtplib
import datetime as dt
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from pathlib import Path

def main():
    user = os.environ.get("GMAIL_USER")
    app_pass = os.environ.get("GMAIL_APP_PASS")
    to_addr = os.environ.get("MAIL_TO")
    if not all([user, app_pass, to_addr]):
        print("错误: 缺少 GMAIL_USER / GMAIL_APP_PASS / MAIL_TO 环境变量", file=sys.stderr)
        sys.exit(1)

    report_path = Path("reports/latest.md")
    if not report_path.exists():
        print("错误: 找不到 reports/latest.md，请先运行 radar.py", file=sys.stderr)
        sys.exit(1)

    body_md = report_path.read_text(encoding="utf-8")
    today = dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%d")

    msg = MIMEMultipart("alternative")
    msg["Subject"] = f"🛰️ AI Coding Radar — {today}"
    msg["From"] = user
    msg["To"] = to_addr

    # 纯文本版（Markdown 原文，任何客户端都能读）
    msg.attach(MIMEText(body_md, "plain", "utf-8"))

    # 简单 HTML 版：把 Markdown 表格转成 <pre> 包裹，保证排版不乱
    html = (
        "<html><body style='font-family:-apple-system,Segoe UI,sans-serif'>"
        "<p>今日 AI Coding Radar 报告（完整内容见下方，或到仓库 reports/ 查看渲染版）：</p>"
        f"<pre style='font-size:13px;line-height:1.5'>{_escape(body_md)}</pre>"
        "</body></html>"
    )
    msg.attach(MIMEText(html, "html", "utf-8"))

    with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
        server.login(user, app_pass)
        server.sendmail(user, [to_addr], msg.as_string())
    print(f"✓ 报告已发送至 {to_addr}")


def _escape(s):
    return (s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;"))


if __name__ == "__main__":
    main()
