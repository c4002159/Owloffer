"""Always-on-top review window: approve jobs, confirm reply drafts, start delivery."""

import json
import queue
import threading
import tkinter as tk
from tkinter import messagebox, ttk

from bosshunter.company_suggestions import block_companies
from bosshunter.config import save_config
from bosshunter.db import add_history, get_db
from bosshunter.fill import fill_review_list
from bosshunter.ui import server_client
from bosshunter.rejected_companies import apply_rejected_companies, mark_jobs_company_rejected
from bosshunter.zombie import apply_zombie_filter
from bosshunter.review_queue import (
    UNCATEGORIZED,
    approve_all,
    approve_jobs,
    approve_reply,
    apply_activity_gate,
    apply_company_dedup,
    apply_match_gate,
    approved_unsent,
    assign_categories,
    automation_settings,
    dispatch_approved,
    job_kind,
    kind_quotas,
    pending_reply_drafts,
    refresh_recruit_scores,
    revoke_approval,
    repeat_flags,
    review_pending,
    skip_jobs,
)

REFRESH_MS = 10_000


def group_by_category(jobs: list[dict]) -> dict[str, list[dict]]:
    """Group review rows by role category, categories sorted by their best score."""
    groups: dict[str, list[dict]] = {}
    for job in jobs:
        groups.setdefault(job.get("category") or UNCATEGORIZED, []).append(job)
    for rows in groups.values():
        rows.sort(key=lambda row: row.get("score") or 0, reverse=True)
    return dict(sorted(groups.items(), key=lambda item: -(item[1][0].get("score") or 0)))


def _fmt(value) -> str:
    return "-" if value is None else str(value)


class ReviewWindow:
    def __init__(self, config: dict, plan: dict | None = None):
        self.config = config
        self.plan = plan
        self.settings = automation_settings(config)
        self.messages: "queue.Queue[str]" = queue.Queue()
        self.dispatching = False
        self.filling = False
        self.server_task_id: str | None = None
        self.stop_event = threading.Event()
        config["_workbench_stop_event"] = self.stop_event

        self.root = tk.Tk()
        self.root.title("BossHunter 审查")
        self.root.geometry("760x520+40+40")
        self.root.attributes("-topmost", True)

        self.status = tk.StringVar(value="加载中…")
        ttk.Label(self.root, textvariable=self.status, anchor="w").pack(fill="x", padx=8, pady=(8, 0))

        notebook = ttk.Notebook(self.root)
        notebook.pack(fill="both", expand=True, padx=8, pady=8)
        self._build_jobs_tab(notebook)
        self._build_approved_tab(notebook)
        self._build_replies_tab(notebook)

        self.log = tk.StringVar(value="")
        ttk.Label(self.root, textvariable=self.log, anchor="w", foreground="#555").pack(fill="x", padx=8, pady=(0, 8))

        self.refresh()
        self.root.after(REFRESH_MS, self._tick)
        self.root.after(500, self._drain_messages)

    # ---- layout -------------------------------------------------------
    def _build_jobs_tab(self, notebook: ttk.Notebook) -> None:
        frame = ttk.Frame(notebook)
        notebook.add(frame, text="待审查岗位")
        columns = ("company", "salary", "match", "activity", "hr", "flags")
        self.jobs_tree = ttk.Treeview(frame, columns=columns, selectmode="extended", height=14)
        self.jobs_tree.tag_configure("flagged", background="#fff3c4")
        self.jobs_tree.heading("#0", text="分类 / 职位")
        for key, title, width in (
            ("company", "公司", 140), ("salary", "薪资", 70), ("match", "匹配度", 55),
            ("activity", "活跃度", 55), ("hr", "HR活跃", 80), ("flags", "标记", 150),
        ):
            self.jobs_tree.heading(key, text=title)
            self.jobs_tree.column(key, width=width, anchor="w")
        self.jobs_tree.column("#0", width=230)
        self.jobs_tree.pack(fill="both", expand=True)

        bar = ttk.Frame(frame)
        bar.pack(fill="x", pady=(6, 0))
        ttk.Button(bar, text="通过全部", command=self.approve_everything).pack(side="left")
        ttk.Button(bar, text="通过所选", command=self.approve_selected).pack(side="left", padx=6)
        ttk.Button(bar, text="跳过所选", command=self.skip_selected).pack(side="left")
        ttk.Button(bar, text="拉黑所选公司…", command=self.block_selected_companies).pack(side="left", padx=6)
        ttk.Button(bar, text="标记公司已拒绝…", command=lambda: self.mark_rejected(self._selected_job_ids())).pack(side="left")
        ttk.Button(bar, text="一键投递已通过岗位", command=self.start_dispatch).pack(side="right")
        self.fill_button = ttk.Button(bar, text=f"补满到 {self.settings['review_cap']} 个", command=self.toggle_fill)
        self.fill_button.pack(side="right", padx=6)

    def _build_approved_tab(self, notebook: ttk.Notebook) -> None:
        """Approved jobs stay visible here with their state, ready for one-click delivery."""
        frame = ttk.Frame(notebook)
        notebook.add(frame, text="已通过待投递")
        columns = ("company", "salary", "match", "activity", "kind", "state")
        self.approved_tree = ttk.Treeview(frame, columns=columns, selectmode="extended", height=14)
        self.approved_tree.heading("#0", text="职位")
        for key, title, width in (
            ("company", "公司", 150), ("salary", "薪资", 70), ("match", "匹配度", 55), ("activity", "活跃度", 55),
            ("kind", "类型", 50), ("state", "状态", 120),
        ):
            self.approved_tree.heading(key, text=title)
            self.approved_tree.column(key, width=width, anchor="w")
        self.approved_tree.column("#0", width=260)
        self.approved_tree.pack(fill="both", expand=True)
        bar = ttk.Frame(frame)
        bar.pack(fill="x", pady=(6, 0))
        ttk.Button(bar, text="撤销通过", command=self.unapprove_selected).pack(side="left")
        ttk.Button(
            bar, text="标记公司已拒绝…", command=lambda: self.mark_rejected(list(self.approved_tree.selection()))
        ).pack(side="left", padx=6)
        ttk.Button(bar, text="一键投递已通过岗位", command=self.start_dispatch).pack(side="right")

    def _build_replies_tab(self, notebook: ttk.Notebook) -> None:
        frame = ttk.Frame(notebook)
        notebook.add(frame, text="回复草稿")
        self.reply_tree = ttk.Treeview(frame, columns=("company", "hr"), height=6, selectmode="browse")
        self.reply_tree.heading("#0", text="职位")
        self.reply_tree.heading("company", text="公司")
        self.reply_tree.heading("hr", text="HR 最新消息")
        self.reply_tree.column("#0", width=200)
        self.reply_tree.column("company", width=150)
        self.reply_tree.column("hr", width=330)
        self.reply_tree.pack(fill="x")
        self.reply_tree.bind("<<TreeviewSelect>>", self._on_reply_select)
        ttk.Label(frame, text="回复内容（可直接修改，确认后由监测在下一轮发送）").pack(anchor="w", pady=(8, 2))
        self.reply_text = tk.Text(frame, height=6, wrap="word")
        self.reply_text.pack(fill="both", expand=True)
        bar = ttk.Frame(frame)
        bar.pack(fill="x", pady=(6, 0))
        ttk.Button(bar, text="确认发送", command=self.confirm_reply).pack(side="left")
        ttk.Button(bar, text="放弃", command=self.dismiss_reply).pack(side="left", padx=6)
        self.drafts: dict[str, dict] = {}

    # ---- data ---------------------------------------------------------
    def refresh(self) -> None:
        db = get_db()
        try:
            if self.plan:
                assign_categories(db, self.plan)
            apply_rejected_companies(db)
            apply_zombie_filter(db, self.config)
            apply_company_dedup(db, self.config)
            apply_match_gate(db, self.config)
            refresh_recruit_scores(db, self.config)
            apply_activity_gate(db, self.config)
            jobs = review_pending(db, self.settings["review_min_score"])
            approved = approved_unsent(db)
            drafts = pending_reply_drafts(db)
            flags = repeat_flags(db, jobs)
        finally:
            db.close()

        selected = set(self.jobs_tree.selection())
        self.jobs_tree.delete(*self.jobs_tree.get_children())
        for category, rows in group_by_category(jobs).items():
            parent = self.jobs_tree.insert("", "end", iid=f"cat:{category}", text=f"{category}（{len(rows)}）", open=True)
            for job in rows:
                labels = flags.get(str(job["id"]), [])
                self.jobs_tree.insert(
                    parent, "end", iid=str(job["id"]), text=job["title"],
                    values=(job["company"], job.get("salary") or "-", _fmt(job.get("score")),
                            _fmt(job.get("recruit_score")),
                            job.get("hr_active") or "-", " ".join(labels)),
                    tags=("flagged",) if labels else (),
                )
        for iid in selected:
            if self.jobs_tree.exists(iid):
                self.jobs_tree.selection_add(iid)

        selected_approved = set(self.approved_tree.selection())
        self.approved_tree.delete(*self.approved_tree.get_children())
        for job in approved:
            has_greeting = bool(str(job.get("greeting") or "").strip())
            self.approved_tree.insert(
                "", "end", iid=str(job["id"]), text=job["title"],
                values=(job["company"], job.get("salary") or "-", _fmt(job.get("score")),
                        _fmt(job.get("recruit_score")),
                        "实习" if job_kind(job) == "internship" else "全职",
                        "已确认·招呼语已备" if has_greeting else "已确认·待投递"),
            )
        for iid in selected_approved:
            if self.approved_tree.exists(iid):
                self.approved_tree.selection_add(iid)

        self.drafts = {str(d["history_id"]): d for d in drafts}
        self.reply_tree.delete(*self.reply_tree.get_children())
        for draft in drafts:
            self.reply_tree.insert(
                "", "end", iid=str(draft["history_id"]), text=draft["title"],
                values=(draft["company"], draft["hr_question"].replace("\n", " ")[:80]),
            )

        quotas = kind_quotas(self.config)
        counts = {"fulltime": 0, "internship": 0}
        for job in [*jobs, *approved]:
            counts[job_kind(job)] += 1
        if quotas["internship"] > 0:
            quota_text = f"全职 {counts['fulltime']}/{quotas['fulltime']} · 实习 {counts['internship']}/{quotas['internship']}"
        else:
            quota_text = f"待审查 {len(jobs)} / 上限 {self.settings['review_cap']}"
        full = all(counts[kind] >= quotas[kind] for kind in quotas if quotas[kind] > 0)
        self.status.set(
            f"{quota_text}{'（已满）' if full else ''}　待审查 {len(jobs)}　已通过待发 {len(approved)}　待确认回复 {len(drafts)}"
        )

    def _tick(self) -> None:
        if not self.dispatching:
            self.refresh()
        self.root.after(REFRESH_MS, self._tick)

    def _selected_job_ids(self) -> list[str]:
        return [iid for iid in self.jobs_tree.selection() if not iid.startswith("cat:")]

    # ---- job actions --------------------------------------------------
    def unapprove_selected(self) -> None:
        ids = list(self.approved_tree.selection())
        if not ids:
            return
        db = get_db()
        try:
            count = revoke_approval(db, ids)
        finally:
            db.close()
        self.log.set(f"已撤销 {count} 个岗位的通过，回到待审查")
        self.refresh()

    def mark_rejected(self, job_ids: list[str]) -> None:
        """These jobs' companies already turned the user down: remember them and never collect them again."""
        job_ids = [job_id for job_id in job_ids if not str(job_id).startswith("cat:")]
        if not job_ids:
            return
        if not messagebox.askyesno(
            "标记公司已拒绝",
            f"把所选 {len(job_ids)} 个岗位的公司标记为“已拒绝”？\n\n"
            "这些公司会记入数据库：之后采集不再进入岗位池，同公司其他待投递岗位也会被过滤。",
            parent=self.root,
        ):
            return
        db = get_db()
        try:
            result = mark_jobs_company_rejected(db, job_ids)
        finally:
            db.close()
        self.log.set(
            f"已记录 {len(result['companies'])} 家公司为已拒绝，标记 {result['marked_jobs']} 个岗位，另过滤 {result['hidden_jobs']} 个同公司岗位"
        )
        self.refresh()

    def approve_everything(self) -> None:
        db = get_db()
        try:
            count = len(review_pending(db, self.settings["review_min_score"]))
        finally:
            db.close()
        if not count:
            return
        if not messagebox.askyesno(
            "通过全部",
            f"将通过当前列表里的 {count} 个岗位。\n"
            "同一家公司（含 华为 / 华为技术 这类写法）只保留得分最高的 1 个，"
            "其余自动过滤；今天已通过过的公司也会被过滤。\n\n"
            "通过只是标记，不会发送任何消息。确认？",
            parent=self.root,
        ):
            return
        db = get_db()
        try:
            approved, duplicates = approve_all(db, self.config)
        finally:
            db.close()
        self.log.set(f"已通过 {approved} 个，{duplicates} 个因同公司重复被过滤")
        self.refresh()

    def toggle_fill(self) -> None:
        if self.filling:
            if self.server_task_id:
                server_client.stop_task(self.server_task_id)
            self.stop_event.set()
            self.log.set("正在停止，当前这一步完成后结束…")
            return
        # Prefer the dashboard server: the task then shows up on the dashboard (running, logs, stop).
        started, payload = server_client.start_task("fill")
        if started:
            self.server_task_id = str(payload["id"])
            self.filling = True
            self.fill_button.config(text="停止补充")
            self.log.set("已在面板里启动“补满审查列表”，仪表盘可看到进度")
            self.root.after(2500, self._poll_server_fill)
            return
        if payload != "面板服务未运行":
            messagebox.showwarning("无法补满", str(payload), parent=self.root)
            return
        self.stop_event.clear()
        self.filling = True
        self.fill_button.config(text="停止补充")
        self.log.set("面板服务未运行，改在本窗口内补充（仪表盘看不到进度；运行 bosshunter web 后可在面板看到）")
        threading.Thread(target=self._fill_worker, daemon=True).start()

    def _poll_server_fill(self) -> None:
        if not (self.filling and self.server_task_id):
            return
        active, last = server_client.workbench_tasks()
        task = active if active and active.get("id") == self.server_task_id else (
            last if last and last.get("id") == self.server_task_id else None
        )
        if task and task.get("logs"):
            self.log.set(str(task["logs"][-1]))
        if task is None or task.get("status") not in {"running", "stopping"}:
            status = (task or {}).get("status", "unknown")
            error = (task or {}).get("error")
            self.filling = False
            self.server_task_id = None
            self.fill_button.config(text=f"补满到 {self.settings['review_cap']} 个")
            if error:
                self.log.set(f"补充中断：{error}")
            elif status == "stopped":
                self.log.set("补充已停止")
            self.refresh()
            return
        self.refresh()
        self.root.after(3000, self._poll_server_fill)

    def _fill_worker(self) -> None:
        try:
            summary = fill_review_list(self.config, report=self.messages.put, stop_event=self.stop_event)
            note = f"补充结束：待审查 {summary['waiting']} / {summary['cap']}"
            if summary["stopped"]:
                note += f"（{summary['stopped']}）"
            self.messages.put(note)
        except Exception as exc:  # show the failure in the window instead of dying silently
            self.messages.put(f"补充中断：{type(exc).__name__}: {exc}")
        finally:
            self.messages.put("__fill_done__")

    def approve_selected(self) -> None:
        ids = self._selected_job_ids()
        if not ids:
            return
        db = get_db()
        try:
            changed = approve_jobs(db, ids)
        finally:
            db.close()
        self.log.set(f"已通过 {changed} 个岗位")
        self.refresh()

    def skip_selected(self) -> None:
        ids = self._selected_job_ids()
        if not ids:
            return
        db = get_db()
        try:
            changed = skip_jobs(db, ids)
        finally:
            db.close()
        self.log.set(f"已跳过 {changed} 个岗位")
        self.refresh()

    def block_selected_companies(self) -> None:
        ids = self._selected_job_ids()
        if not ids:
            return
        placeholders = ",".join("?" for _ in ids)
        db = get_db()
        try:
            rows = db.execute(f"SELECT DISTINCT company FROM jobs WHERE id IN ({placeholders})", ids).fetchall()
            names = sorted({str(row["company"]).strip() for row in rows if str(row["company"]).strip()})
            if not names:
                return
            listing = "\n".join(names[:15]) + ("\n…" if len(names) > 15 else "")
            if not messagebox.askyesno(
                "加入企业黑名单",
                f"以下公司将加入黑名单：\n\n{listing}\n\n"
                "之后不再采集、评分，这些公司待审查的岗位会被移出列表。\n确认加入？",
                parent=self.root,
            ):
                return
            added = block_companies(db, self.config, names)
        finally:
            db.close()
        if added:
            save_config(self.config)
        self.log.set(f"已将 {len(added)} 家公司加入黑名单")
        self.refresh()

    def start_dispatch(self) -> None:
        if self.dispatching:
            return
        db = get_db()
        try:
            count = len(approved_unsent(db))
        finally:
            db.close()
        if count == 0:
            messagebox.showinfo("BossHunter", "还没有已通过的岗位。先选中岗位点“通过所选”。", parent=self.root)
            return
        if not messagebox.askyesno(
            "确认投递",
            f"将为 {count} 个已通过岗位定制简历、生成招呼语，并按限速发送"
            f"（每日上限 {self.config.get('throttle', {}).get('daily_limit', 30)}，只在发送时间窗口内）。\n\n确认开始？",
            parent=self.root,
        ):
            return
        # Prefer the dashboard server: the delivery queue then shows up on the dashboard like any other task.
        started, payload = server_client.start_deliver_approved(tailor_resumes=True)
        if started:
            self.dispatching = True
            self.server_task_id = str(payload.get("id") or "")
            self.log.set("已在面板里启动投递队列（先定制简历，再按限速发送），仪表盘可看到进度")
            self.root.after(3000, self._poll_server_delivery)
            return
        if payload != "面板服务未运行":
            messagebox.showwarning("无法投递", str(payload), parent=self.root)
            return
        self.dispatching = True
        self.log.set("面板服务未运行，改在本窗口内投递（仪表盘看不到进度）")
        threading.Thread(target=self._dispatch_worker, daemon=True).start()

    def _poll_server_delivery(self) -> None:
        if not (self.dispatching and self.server_task_id):
            return
        active, last = server_client.workbench_tasks()
        task = active if active and active.get("id") == self.server_task_id else (
            last if last and last.get("id") == self.server_task_id else None
        )
        if task and task.get("logs"):
            self.log.set(str(task["logs"][-1]))
        if task is None or task.get("status") not in {"running", "stopping"}:
            self.dispatching = False
            self.server_task_id = None
            if (task or {}).get("error"):
                self.log.set(f"投递中断：{task['error']}")
            self.refresh()
            return
        self.root.after(3000, self._poll_server_delivery)

    def _dispatch_worker(self) -> None:
        try:
            dispatch_approved(self.config, report=self.messages.put)
        except Exception as exc:  # report to the window instead of dying silently in the thread
            self.messages.put(f"投递中断：{type(exc).__name__}: {exc}")
        finally:
            self.messages.put("__done__")

    def _drain_messages(self) -> None:
        try:
            while True:
                message = self.messages.get_nowait()
                if message == "__done__":
                    self.dispatching = False
                    self.refresh()
                elif message == "__fill_done__":
                    self.filling = False
                    self.fill_button.config(text=f"补满到 {self.settings['review_cap']} 个")
                    self.refresh()
                else:
                    self.log.set(message)
        except queue.Empty:
            pass
        self.root.after(500, self._drain_messages)

    # ---- reply actions ------------------------------------------------
    def _on_reply_select(self, _event=None) -> None:
        selection = self.reply_tree.selection()
        draft = self.drafts.get(selection[0]) if selection else None
        self.reply_text.delete("1.0", "end")
        if draft:
            self.reply_text.insert("1.0", draft["ai_reply"])

    def _selected_draft(self) -> dict | None:
        selection = self.reply_tree.selection()
        return self.drafts.get(selection[0]) if selection else None

    def confirm_reply(self) -> None:
        draft = self._selected_draft()
        if not draft:
            return
        message = self.reply_text.get("1.0", "end").strip()
        db = get_db()
        try:
            ok = approve_reply(db, draft["history_id"], message)
        finally:
            db.close()
        self.log.set("已确认，监测下一轮会发送" if ok else "确认失败：这条草稿已过期或内容为空")
        self.refresh()

    def dismiss_reply(self) -> None:
        draft = self._selected_draft()
        if not draft:
            return
        from bosshunter.executor.monitor import _build_reply_resolution_detail

        db = get_db()
        try:
            row = db.execute("SELECT detail FROM history WHERE id = ?", (draft["history_id"],)).fetchone()
            add_history(
                db, draft["job_id"], "reply_dismissed",
                _build_reply_resolution_detail(
                    "reply_dismissed.v1", "审查窗口放弃回复建议", row["detail"] if row else "",
                    pending_history_id=int(draft["history_id"]),
                ),
            )
        finally:
            db.close()
        self.reply_text.delete("1.0", "end")
        self.refresh()

    def run(self) -> None:
        self.root.mainloop()


def open_review_window(config: dict, plan: dict | None = None) -> None:
    ReviewWindow(config, plan).run()
