"""Always-on-top review window: approve jobs, confirm reply drafts, start delivery."""

import json
import queue
import threading
import tkinter as tk
from tkinter import messagebox, ttk

from bosshunter.company_suggestions import block_companies
from bosshunter.config import save_config
from bosshunter.db import add_history, get_db
from bosshunter.review_queue import (
    UNCATEGORIZED,
    approve_jobs,
    approve_reply,
    apply_match_gate,
    approved_unsent,
    assign_categories,
    automation_settings,
    dispatch_approved,
    pending_reply_drafts,
    refresh_recruit_scores,
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

        self.root = tk.Tk()
        self.root.title("BossHunter 审查")
        self.root.geometry("760x520+40+40")
        self.root.attributes("-topmost", True)

        self.status = tk.StringVar(value="加载中…")
        ttk.Label(self.root, textvariable=self.status, anchor="w").pack(fill="x", padx=8, pady=(8, 0))

        notebook = ttk.Notebook(self.root)
        notebook.pack(fill="both", expand=True, padx=8, pady=8)
        self._build_jobs_tab(notebook)
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
        columns = ("company", "salary", "score", "match", "recruit", "hr", "flags")
        self.jobs_tree = ttk.Treeview(frame, columns=columns, selectmode="extended", height=14)
        self.jobs_tree.tag_configure("flagged", background="#fff3c4")
        self.jobs_tree.heading("#0", text="分类 / 职位")
        for key, title, width in (
            ("company", "公司", 130), ("salary", "薪资", 60), ("score", "综合", 45),
            ("match", "匹配", 45), ("recruit", "真实度", 50), ("hr", "HR活跃", 70), ("flags", "标记", 140),
        ):
            self.jobs_tree.heading(key, text=title)
            self.jobs_tree.column(key, width=width, anchor="w")
        self.jobs_tree.column("#0", width=230)
        self.jobs_tree.pack(fill="both", expand=True)

        bar = ttk.Frame(frame)
        bar.pack(fill="x", pady=(6, 0))
        ttk.Button(bar, text="通过所选", command=self.approve_selected).pack(side="left")
        ttk.Button(bar, text="跳过所选", command=self.skip_selected).pack(side="left", padx=6)
        ttk.Button(bar, text="拉黑所选公司…", command=self.block_selected_companies).pack(side="left")
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
            apply_match_gate(db, self.config)
            refresh_recruit_scores(db, self.config)
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
                            _fmt(job.get("match_score")), _fmt(job.get("recruit_score")),
                            job.get("hr_active") or "-", " ".join(labels)),
                    tags=("flagged",) if labels else (),
                )
        for iid in selected:
            if self.jobs_tree.exists(iid):
                self.jobs_tree.selection_add(iid)

        self.drafts = {str(d["history_id"]): d for d in drafts}
        self.reply_tree.delete(*self.reply_tree.get_children())
        for draft in drafts:
            self.reply_tree.insert(
                "", "end", iid=str(draft["history_id"]), text=draft["title"],
                values=(draft["company"], draft["hr_question"].replace("\n", " ")[:80]),
            )

        full = len(jobs) + len(approved) >= self.settings["review_cap"]
        self.status.set(
            f"待审查 {len(jobs)} / 上限 {self.settings['review_cap']}"
            f"{'（已满，评分暂停）' if full else ''}　已通过待发 {len(approved)}　待确认回复 {len(drafts)}"
        )

    def _tick(self) -> None:
        if not self.dispatching:
            self.refresh()
        self.root.after(REFRESH_MS, self._tick)

    def _selected_job_ids(self) -> list[str]:
        return [iid for iid in self.jobs_tree.selection() if not iid.startswith("cat:")]

    # ---- job actions --------------------------------------------------
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
        self.dispatching = True
        threading.Thread(target=self._dispatch_worker, daemon=True).start()

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
