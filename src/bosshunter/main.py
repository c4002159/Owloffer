"""BossHunter CLI - 主入口"""

import os
from pathlib import Path

import click
from rich.console import Console

from bosshunter import __version__
from bosshunter.config import load_config, migrate_legacy_credentials

console = Console()
GITHUB_URL = "https://github.com/powerycy/BossHunter"


def _hint_web():
    """Print a one-line hint about the web dashboard."""
    console.print("[dim]💡 运行 bosshunter web 可打开可视化看板[/dim]")


def _project_root() -> Path:
    """Return the installed project root used for config.yaml and runtime data."""
    return Path(__file__).resolve().parents[2]


def _runtime_base_dir(config_path: Path | None = None) -> Path:
    """Resolve where config.yaml and data/ should be read from."""
    if config_path is not None:
        return config_path.resolve().parent
    root = _project_root()
    if (root / "config.yaml").exists():
        return root
    if Path("config.yaml").exists():
        return Path.cwd()
    return Path.cwd()


def _hint_star_support(base_dir: Path | None = None):
    """Print a lightweight GitHub Star support hint once per local checkout."""
    marker = (base_dir or _runtime_base_dir()) / "data" / f".star_hint_{__version__}"
    if marker.exists():
        return
    console.print(f"[dim]⭐ 如果 BossHunter 帮你节省了求职时间，欢迎 Star 支持继续维护：{GITHUB_URL}[/dim]")
    try:
        marker.parent.mkdir(parents=True, exist_ok=True)
        marker.write_text("shown\n", encoding="utf-8")
    except OSError:
        pass


def _is_first_run(config_path: Path | None = None) -> bool:
    """Check if this is the first run (no config.yaml exists)."""
    path = config_path or _runtime_base_dir() / "config.yaml"
    return not path.exists()


def _prompt_setup(port: int = 8686) -> None:
    """Show first-run setup prompt pointing to Web Dashboard."""
    console.print()
    console.print("[bold cyan]═══ 欢迎使用 BossHunter ═══[/bold cyan]")
    console.print()
    console.print("[yellow]检测到尚未配置，建议先进入 Web 端完成初始设置：[/yellow]")
    console.print()
    console.print(f"  [bold]bosshunter web[/bold]  →  打开配置面板 (http://127.0.0.1:{port})")
    console.print()
    console.print("[dim]在面板中可以设置：[/dim]")
    console.print("[dim]  • 简历路径、期望薪资、一票否决词[/dim]")
    console.print("[dim]  • 搜索关键词、目标城市[/dim]")
    console.print("[dim]  • AI 评分阈值、发送频率限制[/dim]")
    console.print("[dim]  • 发送时间窗口、每日上限[/dim]")
    console.print()
    console.print("[dim]如需跳过，可手动创建 config.yaml（参考 config.example.yaml）[/dim]")
    console.print()


@click.group(name="bosshunter", invoke_without_command=True)
@click.version_option(version=__version__, prog_name="bosshunter")
@click.option("--config", "config_path", default=None, type=click.Path(exists=False), help="配置文件路径（默认 config.yaml）")
@click.pass_context
def cli(ctx: click.Context, config_path: str | None) -> None:
    """BossHunter - 某直聘智能求职Agent"""
    ctx.ensure_object(dict)
    path = Path(config_path).resolve() if config_path else None
    base_dir = _runtime_base_dir(path)
    if path is None and (base_dir / "config.yaml").exists():
        path = base_dir / "config.yaml"
    ctx.obj["base_dir"] = base_dir
    os.chdir(base_dir)
    if path is not None:
        migrate_legacy_credentials(path)
    ctx.obj["config"] = load_config(path)
    from bosshunter.browser import configure
    configure(ctx.obj["config"])

    # First run: no subcommand and no config → prompt setup
    if ctx.invoked_subcommand is None:
        if _is_first_run(path):
            _prompt_setup()
        else:
            console.print("[bold cyan]BossHunter[/bold cyan] 已就绪")
            console.print("[dim]运行 bosshunter --help 查看可用命令[/dim]")
            _hint_web()


@cli.command()
@click.pass_context
def connect(ctx: click.Context) -> None:
    """检测 BossHunter Browser Runtime 与 Chrome 连接"""
    from bosshunter.browser import configure
    from bosshunter.browser.diagnostics import print_browser_diagnostics

    config = ctx.obj["config"]
    configure(config)
    ok = print_browser_diagnostics(config, console)
    if not ok:
        raise SystemExit(1)

    console.print("\n[bold green]连接检测完成[/bold green]")


@cli.command(name="github-sync")
@click.option("--user", "username", default=None, help="GitHub 用户名（默认读取 profile.github_username）")
@click.pass_context
def github_sync(ctx: click.Context, username: str | None) -> None:
    """读取公开 GitHub 仓库并缓存技术栈档案，供评分时和简历双比对"""
    from bosshunter.github_profile import fetch_profile, save_profile, summarize_profile

    config = ctx.obj["config"]
    username = username or (config.get("profile", {}) or {}).get("github_username", "")
    if not username:
        console.print("[red]请用 --user 指定 GitHub 用户名，或在 config.yaml 的 profile.github_username 中填写[/red]")
        raise SystemExit(1)
    try:
        profile = fetch_profile(str(username))
    except Exception as exc:  # network or API failure: report, never crash the CLI
        console.print(f"[red]读取 GitHub 失败: {type(exc).__name__}: {exc}[/red]")
        raise SystemExit(1)
    path = save_profile(profile, ctx.obj["base_dir"])
    console.print(f"[green]✓ 已缓存 {len(profile['repos'])} 个仓库 → {path}[/green]")
    console.print(summarize_profile(profile))


@cli.command()
@click.option("--apply", "apply_tiers", default=None, help="把指定层写入 config.yaml 的搜索关键词，如 core 或 core,expand")
@click.option("--stats", is_flag=True, help="只显示各关键词的产出统计，不调用 AI")
@click.pass_context
def keywords(ctx: click.Context, apply_tiers: str | None, stats: bool) -> None:
    """让关键词模型读简历和 GitHub 档案，生成核心/扩展/探索三层搜索词"""
    from bosshunter.ai import keywords as kw
    from bosshunter.ai.scorer import _load_resume
    from bosshunter.config import save_config
    from bosshunter.db import get_db

    config = ctx.obj["config"]
    base_dir = ctx.obj["base_dir"]

    if stats:
        db = get_db()
        try:
            rows = kw.keyword_yield(db, int(config.get("scoring", {}).get("threshold", 60)))
        finally:
            db.close()
        for row in rows:
            console.print(f"{row['keyword']}: 采集 {row['collected']}，达标 {row['qualified']}（{row['rate']:.0%}）")
        if not rows:
            console.print("[yellow]还没有带关键词来源的岗位数据[/yellow]")
        return

    if apply_tiers:
        plan = kw.load_plan(base_dir)
        if not plan:
            console.print("[red]还没有关键词方案，请先不带 --apply 运行一次 bosshunter keywords[/red]")
            raise SystemExit(1)
        tiers = tuple(t.strip() for t in apply_tiers.split(",") if t.strip())
        unknown = [t for t in tiers if t not in kw.TIERS]
        if unknown:
            console.print(f"[red]未知层: {', '.join(unknown)}（可选 core / expand / explore）[/red]")
            raise SystemExit(1)
        chosen = kw.select_keywords(plan, tiers)
        config.setdefault("search", {})["keywords"] = chosen
        config.setdefault("platforms", {}).setdefault("boss", {}).setdefault("search", {})["keywords"] = chosen
        profile = config.setdefault("profile", {})
        merged = list(dict.fromkeys([*(profile.get("deal_breakers") or []), *plan.get("excludes", [])]))
        profile["deal_breakers"] = merged
        save_config(config)
        console.print(f"[green]✓ 已写入 {len(chosen)} 个搜索词，并合并 {len(plan.get('excludes', []))} 个排除词[/green]")
        console.print("、".join(chosen))
        return

    resume = _load_resume(config)
    if not resume:
        console.print("[red]无法读取简历文件[/red]")
        raise SystemExit(1)
    plan = kw.generate_plan(config, resume)
    if not plan:
        console.print("[red]未能生成关键词方案：请检查 AI 配置（bosshunter ai-status）[/red]")
        raise SystemExit(1)
    path = kw.save_plan(plan, base_dir)
    for direction in plan["directions"]:
        console.print(f"[bold]{direction['name']}[/bold] [dim]{direction['evidence']}[/dim]")
        console.print("  " + "、".join(direction["keywords"]))
    for tier, label in (("core", "核心词"), ("expand", "扩展词"), ("explore", "探索词")):
        console.print(f"[cyan]{label}[/cyan]: " + "、".join(plan[tier]))
    console.print(f"[dim]排除词: {'、'.join(plan['excludes'])}[/dim]")
    console.print(f"[green]✓ 方案已保存 → {path}；确认后运行 bosshunter keywords --apply core,expand[/green]")


@cli.command()
@click.option("--per-keyword", default=20, show_default=True, help="每个关键词每次最多采集的岗位数")
@click.option("--rounds", default=3, show_default=True, help="最多循环几轮关键词")
@click.pass_context
def fill(ctx: click.Context, per_keyword: int, rounds: int) -> None:
    """一次补满审查列表（采集→评分→去重过滤，直到达到 automation.review_cap）"""
    from bosshunter.fill import fill_review_list

    summary = fill_review_list(ctx.obj["config"], console.print, per_keyword=per_keyword, max_rounds=rounds)
    console.print(f"[green]待审查 {summary['waiting']} / {summary['cap']}，本次新采集 {summary['collected']} 个[/green]")
    if summary["stopped"]:
        console.print(f"[yellow]{summary['stopped']}[/yellow]")


@cli.command()
@click.pass_context
def review(ctx: click.Context) -> None:
    """打开置顶审查窗口：通过/跳过岗位、确认回复草稿、一键投递"""
    from bosshunter.ai.keywords import load_plan
    from bosshunter.ui.review_window import open_review_window

    open_review_window(ctx.obj["config"], load_plan(ctx.obj["base_dir"]))


@cli.command(name="ai-status")
@click.pass_context
def ai_status(ctx: click.Context) -> None:
    """安全检测 AI 服务配置与连接状态（不显示 API Key）"""
    from bosshunter.web.preflight import check_ai_connection

    checks = check_ai_connection(ctx.obj["config"], required=True)
    has_error = False
    for check in checks:
        status = check.get("status")
        if status == "pass":
            marker = "[green]✓[/green]"
        elif status == "warning":
            marker = "[yellow]![/yellow]"
        else:
            marker = "[red]✗[/red]"
            has_error = True
        console.print(f"{marker} {check.get('message', 'AI 检测结果')}")
        console.print(f"  [dim]{check.get('detail', '')}[/dim]")
    if has_error:
        raise SystemExit(1)


@cli.command()
@click.option("--keyword", "-k", default=None, help="搜索关键词（覆盖配置文件）")
@click.option("--limit", "-l", default=None, type=int, help="最多抓取岗位数（默认不限制）")
@click.pass_context
def scrape(ctx: click.Context, keyword: str | None, limit: int | None) -> None:
    """采集岗位信息"""
    from bosshunter.scraper.jobs import scrape_jobs

    config = ctx.obj["config"]
    keywords = [keyword] if keyword else config["search"]["keywords"]

    console.print(f"[bold]开始采集岗位...[/bold] 关键词: {keywords}")
    count = scrape_jobs(config, keywords, limit)
    console.print(f"[green]✓[/green] 采集完成，共获取 {count} 个新岗位")
    _hint_web()
    _hint_star_support()


@cli.command()
@click.option("--rescore-filtered", is_flag=True, help="同时重新评分之前被 AI 判为低分的岗位")
@click.pass_context
def score(ctx: click.Context, rescore_filtered: bool) -> None:
    """对采集的岗位进行AI评分"""
    from bosshunter.ai.scorer import score_jobs

    config = ctx.obj["config"]
    console.print("[bold]开始AI评分...[/bold]")
    scored, filtered = score_jobs(config, rescore_filtered=rescore_filtered)
    console.print(f"[green]✓[/green] 评分完成: {scored} 个通过, {filtered} 个过滤")
    _hint_star_support()


@cli.command()
@click.pass_context
def greet(ctx: click.Context) -> None:
    """为已确认的岗位生成招呼语"""
    from bosshunter.ai.greeter import generate_greetings

    config = ctx.obj["config"]
    console.print("[bold]生成招呼语...[/bold]")
    count = generate_greetings(config)
    console.print(f"[green]✓[/green] 已生成 {count} 条招呼语")
    _hint_star_support()


@cli.command()
@click.pass_context
def confirm(ctx: click.Context) -> None:
    """展示投递清单并确认"""
    from bosshunter.ui.confirm import show_confirmation

    config = ctx.obj["config"]
    show_confirmation(config)


@cli.command()
@click.option("--force", is_flag=True, help="跳过随机休息日检查")
@click.pass_context
def send(ctx: click.Context, force: bool) -> None:
    """自动发送已生成的招呼语"""
    from bosshunter.executor.sender import send_greetings

    config = ctx.obj["config"]
    console.print("[bold]开始发送招呼语...[/bold]")
    sent = send_greetings(config, force=force)
    console.print(f"[green]✓[/green] 发送完成: {sent} 条")
    _hint_web()
    _hint_star_support()


@cli.command()
@click.option("--full", is_flag=True, help="完整仪表盘视图")
@click.pass_context
def status(ctx: click.Context, full: bool) -> None:
    """查看投递状态统计"""
    if full:
        from bosshunter.tracker.status import show_dashboard
        show_dashboard()
    else:
        from bosshunter.tracker.status import show_status
        show_status()


@cli.command()
@click.pass_context
def run(ctx: click.Context) -> None:
    """一键运行完整流程: 采集→评分→确认→招呼语→发送"""
    from bosshunter.pipeline import run_pipeline

    config = ctx.obj["config"]
    console.print("[bold cyan]═══ BossHunter 启动 ═══[/bold cyan]\n")
    run_pipeline(config)
    _hint_web()
    _hint_star_support()


@cli.command()
@click.option("--job-id", default=None, help="指定岗位ID生成简历")
@click.pass_context
def resume(ctx: click.Context, job_id: str | None) -> None:
    """为指定岗位生成定制简历PDF"""
    from bosshunter.ai.resume import generate_tailored_resume

    config = ctx.obj["config"]
    if job_id:
        generate_tailored_resume(job_id, config)
    else:
        console.print("[yellow]请指定 --job-id[/yellow]")


@cli.command()
@click.option("--once", is_flag=True, help="只检查一次（不循环）")
@click.option("--interval", default=None, type=int, help="循环检查间隔(分钟)，默认读取配置文件")
@click.pass_context
def monitor(ctx: click.Context, once: bool, interval: int | None) -> None:
    """监控HR回复，自动回复或发送简历"""
    from bosshunter.executor.monitor import (
        get_effective_monitor_interval_minutes,
        monitor_and_send_resumes,
    )

    config = ctx.obj["config"]
    interval_min = get_effective_monitor_interval_minutes(config, interval)
    interval_sec = interval_min * 60

    if once:
        console.print("[bold cyan]═══ 单次监听模式 ═══[/bold cyan]\n")
        summary = monitor_and_send_resumes(config)
        parts = [
            f"自动回复{summary.get('replied', 0)}条",
            f"跳过{summary.get('skipped', 0)}条",
        ]
        if summary.get("needs_resume"):
            parts.append(f"[bold yellow]待手动发简历{summary['needs_resume']}份[/bold yellow]")
        if summary.get("follow_up"):
            parts.append(f"跟进{summary['follow_up']}条")
        if summary.get("rejected"):
            parts.append(f"拒绝{summary['rejected']}条")
        console.print(f"\n[bold]本次: {', '.join(parts)}[/bold]")
        if summary.get("stop_reason"):
            console.print("[red]检测到平台风险信号，本次监测已安全停止[/red]")
    else:
        console.print(f"[bold cyan]═══ 持续监听模式 (间隔 {interval_min:g} 分钟) ═══[/bold cyan]\n")
        console.print("[dim]按 Ctrl+C 停止[/dim]\n")
        try:
            while True:
                try:
                    summary = monitor_and_send_resumes(config)
                    if summary.get("stop_reason"):
                        console.print("[red]检测到平台风险信号，持续监测已安全停止[/red]")
                        break
                except Exception as e:
                    console.print(f"[red]本轮监听出错: {e}[/red]")
                    console.print("[dim]将在下一轮重试...[/dim]")
                console.print(f"\n[dim]等待 {interval_min:g} 分钟后再次检查...[/dim]\n")
                import time
                time.sleep(interval_sec)
        except KeyboardInterrupt:
            console.print("\n[yellow]已停止监听[/yellow]")


@cli.command()
@click.option("--port", "-p", default=8686, help="服务端口（默认 8686）")
@click.option("--no-open", is_flag=True, help="不自动打开浏览器")
@click.pass_context
def web(ctx: click.Context, port: int, no_open: bool) -> None:
    """启动 Web Dashboard（本地看板 + 配置管理）"""
    from bosshunter.web.server import run_server, set_base_dir

    set_base_dir(ctx.obj["base_dir"])
    console.print("[bold cyan]═══ BossHunter Web Dashboard ═══[/bold cyan]")
    console.print(f"[dim]http://127.0.0.1:{port}[/dim]")
    _hint_star_support(ctx.obj["base_dir"])
    console.print()
    run_server(host="127.0.0.1", port=port, open_browser=not no_open)


if __name__ == "__main__":
    cli()
