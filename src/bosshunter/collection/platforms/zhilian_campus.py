"""智联校园（xiaoyuan.zhaopin.com）校招 / 实习岗位采集。

和普通智联招聘分开的另一条采集通道：

* 搜索页没有"职位链接"，列表由页面自己向 ``searchPositions`` 接口要数据（每页 20 条，共 30 页上限）。采集器先让页面发一次
  请求（点一下页码或搜索按钮），记下这次请求的地址、请求头和请求体，然后用同样的请求换页码、换关键词，直接拿到每页的岗位
  （职位名、公司、薪资、城市、学历、校招/实习、发布时间、职位编号）。
* 先用这些列表信息筛选（公司已投过/已在池里、城市、薪资、职位名是否相关），只给留下来的岗位打开详情页
  （``/job/<编号>``）读岗位描述和"更新日期"，再按校招网申的新鲜度规则（默认 30 天内）过滤。
* 岗位以 ``zhilian_campus`` 平台入库（只能手动投递，进"手动投递"列表；普通智联岗位将来可以由程序投递）。
"""

from __future__ import annotations

import json
import time
from typing import Any
from urllib.parse import quote

from bosshunter.collection.base import CollectionBlockedError, CollectionError, CollectorHooks, hours_since_midnight
from bosshunter.platform_safety import apply_pacing, pacing_multiplier
from bosshunter.collection.models import JobCandidate, PlatformCollectionRequest, PlatformCollectionResult
from bosshunter.collection.platforms.zhilian import ZhilianCollector, _updated_days, update_age_reason
from bosshunter.db import (
    delete_page_progress, get_collected_combos, get_page_progress, mark_combo_collected, prune_collected_combos,
    prune_page_progress, upsert_page_progress,
)
from bosshunter.throttle import SendWindowChecker, should_take_day_off

SEARCH_URL = "https://xiaoyuan.zhaopin.com/search/index?refcode=4404&city={city}&cateType=major&query={query}"
DETAIL_URL = "https://xiaoyuan.zhaopin.com/job/{number}"
LIST_MAX_PAGES = 30                      # the site lists 30 pages of 20 at most
TEMPLATE_WAIT_SECONDS = 8.0

# Remembers the request the page makes for a list page (address, headers, body) so it can be asked again for other pages.
JS_HOOK_SEARCH = r"""
(() => {
  if (window.__campusHook) { window.__campusReq = null; return 'rearmed'; }
  window.__campusHook = true; window.__campusReq = null;
  const open = XMLHttpRequest.prototype.open, send = XMLHttpRequest.prototype.send, header = XMLHttpRequest.prototype.setRequestHeader;
  XMLHttpRequest.prototype.open = function (m, u) { this.__u = u; this.__h = {}; return open.apply(this, arguments); };
  XMLHttpRequest.prototype.setRequestHeader = function (k, v) { if (this.__h) this.__h[k] = v; return header.apply(this, arguments); };
  XMLHttpRequest.prototype.send = function (body) {
    if (String(this.__u).includes('searchPositions')) window.__campusReq = {u: String(this.__u), h: this.__h, body: String(body)};
    return send.apply(this, arguments);
  };
  return 'hooked';
})()
"""

# Makes the page ask for a list page once: the pager's page "2" when there is one, otherwise the search button.
JS_TRIGGER_SEARCH = r"""
(() => {
  const pager = Array.from(document.querySelectorAll('[class*=pagination__number]')).find((e) => (e.innerText || '').trim() === '2');
  if (pager) { pager.scrollIntoView({block: 'center'}); pager.click(); return 'pager'; }
  const button = Array.from(document.querySelectorAll('button, a, div')).find((e) => e.children.length === 0 && (e.innerText || '').trim() === '搜索');
  if (button) { button.click(); return 'search'; }
  return 'none';
})()
"""

JS_READ_TEMPLATE = "JSON.stringify(window.__campusReq || null)"

JS_REPLAY = r"""
(async () => {
  const req = window.__campusReq;
  if (!req) return JSON.stringify({error: 'no_template'});
  const body = JSON.parse(req.body);
  body.pageIndex = __PAGE__;
  body.S_SOU_FULL_INDEX = __KEYWORD__;
  body.S_SOU_WORK_CITY = __CITY__;
  try {
    const response = await fetch(req.u, {method: 'POST', headers: req.h, body: JSON.stringify(body), credentials: 'include'});
    const text = await response.text();
    return JSON.stringify({status: response.status, body: text});
  } catch (e) {
    return JSON.stringify({error: String(e)});
  }
})()
"""

JS_EXTRACT_DETAIL = r"""
(() => {
  const text = document.body ? document.body.innerText : '';
  const blocked = text.match(/验证码|滑块|访问频繁|频率限制|账号异常|拒绝访问/);
  const updated = text.match(/(\d{4}-\d{2}-\d{2})\s*更新/);
  const start = text.indexOf('职位描述');
  let jd = '';
  if (start >= 0) {
    const rest = text.slice(start + 4);
    const stop = rest.search(/待完善在线简历|工作地点|公司简介/);
    jd = (stop > 0 ? rest.slice(0, stop) : rest.slice(0, 4000)).trim();
  }
  const loginWall = /请先登录|请登录|登录后(?:查看|继续)|扫码登录/.test(text) && !jd;
  const title = document.querySelector('h1, [class*=job-name], [class*=position-name]');
  return JSON.stringify({
    status: blocked ? 'blocked' : jd ? 'ready' : loginWall ? 'login_required' : 'selector_changed',
    updated: updated ? updated[1] : '', jd, title: title ? title.innerText.trim() : '',
  });
})()
"""


def template_from_capture(request: dict[str, Any] | None) -> dict[str, Any] | None:
    """The replay template (``u``, ``h``, ``body``) from a request seen on the network; only the headers a page script may set are kept."""
    if not isinstance(request, dict) or not request.get("url") or not request.get("postData"):
        return None
    headers = {key: str(value) for key, value in (request.get("headers") or {}).items()
               if str(key).lower().startswith("x-zp-") or str(key).lower() in ("content-type", "accept")}
    return {"u": str(request["url"]), "h": headers, "body": str(request["postData"])}


def _js_literal(value: Any) -> str:
    return json.dumps(value if isinstance(value, (int, float)) else str(value), ensure_ascii=False)


def parse_item(item: dict[str, Any], keyword: str) -> JobCandidate | None:
    """A ``searchPositions`` list item as a candidate (None when it lacks the job number or the title)."""
    number = str(item.get("number") or "").strip()
    title = str(item.get("name") or "").strip()
    company = str(item.get("companyName") or "").strip()
    if not number or not title:
        return None
    place = "·".join(part for part in (item.get("workCity"), item.get("cityDistrict"), item.get("streetName")) if part)
    published = str(item.get("publishTime") or "")
    return JobCandidate(
        platform="zhilian_campus", source_job_id=number, title=title, company=company,
        salary=str(item.get("salary60") or item.get("salaryReal") or "").strip(), city=place,
        experience=str(item.get("workingExp") or "").strip(), education=str(item.get("education") or "").strip(),
        recruitment_type="campus", url=DETAIL_URL.format(number=number), source_keyword=keyword,
        company_size=str(item.get("companySize") or ""), company_industry=str(item.get("industryName") or ""),
        updated_days=_updated_days(published[:10]) if published else None,
    )


class ZhilianCampusCollector(ZhilianCollector):
    """Campus / internship postings of 智联校园 (the second Zhilian channel)."""

    platform = "zhilian_campus"

    # ------------------------------------------------------------------ the list requests
    def _capture_template(self, target_id: str) -> bool:
        """Make the page ask for a list page once and keep that request. False when it never does."""
        self.browser.evaluate(target_id, JS_HOOK_SEARCH)
        capture, captured = getattr(self.browser, "capture_action", None), getattr(self.browser, "captured_action", None)
        watching = bool(capture and captured and capture(target_id, "searchPositions"))
        deadline = time.monotonic() + TEMPLATE_WAIT_SECONDS
        triggered = False
        while time.monotonic() < deadline:
            if not triggered:
                triggered = self.browser.evaluate(target_id, JS_TRIGGER_SEARCH) in ("pager", "search", '"pager"', '"search"')
            raw = self.browser.evaluate(target_id, JS_READ_TEMPLATE)
            if raw and raw != "null":
                return True
            if watching:                                               # the page's own request is seen by the network layer even when its scripts hide it
                template = template_from_capture(captured(target_id))
                if template:
                    self.browser.evaluate(target_id, f"window.__campusReq = {json.dumps(template, ensure_ascii=False)}; 'ok'")
                    return True
            self.sleep(0.5)
        return False

    def _fetch_page(self, target_id: str, keyword: str, city_code: str, page: int) -> dict[str, Any]:
        script = (JS_REPLAY.replace("__PAGE__", str(int(page))).replace("__KEYWORD__", _js_literal(keyword))
                  .replace("__CITY__", _js_literal(city_code)))
        raw = self.browser.evaluate(target_id, script)
        try:
            wrapper = json.loads(raw) if isinstance(raw, str) else raw
        except json.JSONDecodeError:
            wrapper = None
        if not isinstance(wrapper, dict) or wrapper.get("error"):
            raise CollectionError("selector_changed", f"智联校园列表请求失败：{(wrapper or {}).get('error', '无返回')}")
        status = int(wrapper.get("status") or 0)
        if status in (401, 403, 429):
            raise CollectionBlockedError("rate_limit", f"智联校园列表请求被拒绝（HTTP {status}），可能是限流或登录失效")
        try:
            payload = json.loads(str(wrapper.get("body") or ""))
        except json.JSONDecodeError as exc:
            raise CollectionError("selector_changed", "智联校园列表返回的不是 JSON") from exc
        data = payload.get("data") if isinstance(payload, dict) else None
        if not isinstance(data, dict) or not isinstance(data.get("list"), list):
            raise CollectionError("selector_changed", "智联校园列表返回格式变了")
        return data

    # ------------------------------------------------------------------ the detail page
    def _read_detail(self, hooks: CollectorHooks, candidate: JobCandidate) -> dict[str, Any]:
        target = self.browser.new_tab(candidate.url, background=True)
        if not target:
            raise CollectionError("browser_disconnected", "无法打开智联校园详情页")
        try:
            self.browser.wait_for_load(target, timeout=15)
            detail: dict[str, Any] = {}
            for attempt in range(6):
                raw = self.browser.evaluate(target, JS_EXTRACT_DETAIL)
                detail = json.loads(raw) if isinstance(raw, str) else (raw or {})
                if detail.get("status") in {"ready", "blocked", "login_required"}:
                    break
                self.sleep(1.0)
            return detail
        finally:
            self.browser.close_tab(target)

    # ------------------------------------------------------------------ the collection
    def collect(self, request: PlatformCollectionRequest, hooks: CollectorHooks) -> PlatformCollectionResult:
        apply_pacing(self, pacing_multiplier(self.config, self.platform))
        if any(not str(request.city_codes.get(city) or "").strip() for city in request.cities):
            return PlatformCollectionResult(self.platform, "failed", "no_valid_city", "智联校园城市编码未配置")
        throttle_cfg = self.config.get("throttle", {}) if isinstance(self.config.get("throttle"), dict) else {}
        windows = throttle_cfg.get("collect_windows", ["08:00-23:00"])
        if not SendWindowChecker(windows).is_active():
            return PlatformCollectionResult(self.platform, "completed", "outside_window", f"当前不在采集时间窗口内（{windows}）")
        if should_take_day_off(float(throttle_cfg.get("day_off_probability", 0.05))):
            return PlatformCollectionResult(self.platform, "completed", "day_off", "今日随机休息，跳过智联校园采集")

        ttl = self._resume_ttl_hours()
        collected: set[tuple[str, str]] = set()
        if self.safety_conn is not None:
            prune_collected_combos(self.safety_conn, self.platform, set(request.keywords))
            prune_page_progress(self.safety_conn, self.platform, set(request.keywords))
            collected = get_collected_combos(self.safety_conn, self.platform, within_hours=ttl)

        for city in request.cities:
            code = str(request.city_codes[city]).strip()
            for keyword in request.keywords:
                if (city, keyword) in collected:
                    hooks.on_event(phase="completed_keyword", keyword=keyword, city=city,
                                   message=f"智联校园 {keyword} 今天已采完，整词跳过")
                    continue
                saved = (get_page_progress(self.safety_conn, self.platform, city, keyword, within_hours=ttl)
                         if self.safety_conn is not None else 0)
                start_page = max(saved + 1 if saved > 0 else 1, request.first_page)
                if start_page > min(request.max_pages, LIST_MAX_PAGES):
                    continue
                result = self._collect_keyword(request, hooks, city, code, keyword, start_page)
                if result is not None:
                    return result
        return PlatformCollectionResult(self.platform, "completed", "search_exhausted", "智联校园搜索结果已采集完毕")

    def _collect_keyword(
        self, request: PlatformCollectionRequest, hooks: CollectorHooks, city: str, code: str, keyword: str, start_page: int,
    ) -> PlatformCollectionResult | None:
        """One keyword in one city. None = go on with the next keyword; a result ends the whole collection."""
        target_id = self.browser.new_tab("about:blank", background=True)
        if not target_id:
            return PlatformCollectionResult(self.platform, "failed", "browser_disconnected", "无法打开智联校园搜索页")
        try:
            url = SEARCH_URL.format(city=quote(code), query=quote(keyword))
            if self.browser.navigate_action is not None and not self.browser.navigate_action(target_id, url):
                return PlatformCollectionResult(self.platform, "failed", "browser_disconnected", "智联校园搜索页导航失败")
            self.browser.wait_for_load(target_id, timeout=15)
            self.sleep(2.0)
            if not self._capture_template(target_id):
                return PlatformCollectionResult(self.platform, "blocked", "selector_changed", "智联校园搜索页没有发出列表请求，可能是页面结构变化")
            detail_requests = 0
            last_page = min(request.max_pages, LIST_MAX_PAGES)
            for page in range(start_page, last_page + 1):
                if hooks.stop_event is not None and hooks.stop_event.is_set():
                    return PlatformCollectionResult(self.platform, "stopped", "user_stopped", "用户已停止")
                hooks.on_event(phase="loading_list", keyword=keyword, city=city, page=page)
                try:
                    data = self._fetch_page(target_id, keyword, code, page)
                except CollectionBlockedError as exc:
                    return PlatformCollectionResult(self.platform, "blocked", exc.code, exc.message)
                except CollectionError as exc:
                    return PlatformCollectionResult(self.platform, "blocked", exc.code, exc.message)
                items = [item for item in data["list"] if isinstance(item, dict)]
                if not items:
                    break
                for item in items:
                    candidate = parse_item(item, keyword)
                    if candidate is None:
                        continue
                    outcome = self._process_item(hooks, candidate, city, page, keyword, detail_requests)
                    if isinstance(outcome, PlatformCollectionResult):
                        return outcome
                    detail_requests += outcome
                if self.safety_conn is not None:
                    upsert_page_progress(self.safety_conn, self.platform, city, keyword, page)
                if data.get("isEndPage") or page * 20 >= int(data.get("count") or 0):
                    break
            if self.safety_conn is not None:
                mark_combo_collected(self.safety_conn, self.platform, city, keyword)
                delete_page_progress(self.safety_conn, self.platform, city, keyword)
            return None
        finally:
            self.browser.close_tab(target_id)

    def _process_item(
        self, hooks: CollectorHooks, candidate: JobCandidate, city: str, page: int, keyword: str, detail_requests: int,
    ) -> int | PlatformCollectionResult:
        """List-stage checks, then the detail page. Returns how many detail pages were opened (0 or 1), or the result that
        ends the collection."""
        if hooks.on_list_company is not None and candidate.company:
            if hooks.on_list_company(candidate.company, candidate.city, candidate.title):
                return 0
        reason = self._salary_block_reason(candidate) or self._filter_reason(candidate) or self._relevance_reason(candidate)
        if reason:
            hooks.on_event(message=f"智联校园 列表预筛：{reason}", increment_filtered=True, filtered_candidate=candidate)
            return 0
        if not hooks.on_list_candidate(candidate):
            return 0
        if detail_requests:
            delay = max(0.0, self.uniform(*self.delay_range))
            hooks.on_event(phase="pacing", keyword=keyword, city=city, page=page, message=f"详情页安全间隔 {delay:.1f} 秒")
            if hooks.stop_event is not None:
                if hooks.stop_event.wait(delay):
                    return PlatformCollectionResult(self.platform, "stopped", "user_stopped", "用户已停止")
            else:
                self.sleep(delay)
        hooks.on_event(phase="loading_detail", keyword=keyword, city=city, page=page)
        try:
            detail = self._read_detail(hooks, candidate)
        except CollectionError as exc:
            hooks.on_parse_failed(str(exc) or "智联校园详情页读取失败")
            return 1
        status = detail.get("status")
        if status == "blocked":
            return PlatformCollectionResult(self.platform, "blocked", "rate_limit", "智联校园页面出现验证或限流，已停止整个采集队列")
        if status == "login_required":
            return PlatformCollectionResult(self.platform, "blocked", "login_required", "智联校园要求登录，已停止智联校园当前任务")
        if status != "ready":
            hooks.on_parse_failed("智联校园详情页没有读到岗位描述")
            return 1
        jd = str(detail.get("jd") or "").strip()
        final = JobCandidate(**{**candidate.__dict__, "jd": jd,
                                "updated_days": _updated_days(detail.get("updated")) if detail.get("updated") else candidate.updated_days})
        reason = update_age_reason(self.config, {"updated": detail.get("updated"), "campus": True})
        if not reason:
            reason = self._filter_reason(final)
        if reason:
            hooks.on_event(message=f"智联校园 {reason}", increment_filtered=True, filtered_candidate=final)
            return 1
        if not hooks.on_candidate(final):
            return PlatformCollectionResult(self.platform, "completed", "callback_stopped", "采集回调已停止")
        return 1
