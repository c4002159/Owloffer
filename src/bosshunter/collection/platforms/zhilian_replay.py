"""Ask a Zhilian site for more list pages with the page's own request.

The Zhilian pages load their lists from a ``POST`` request that carries the user's session values (resume number, tokens). A
collector cannot invent those, so it lets the page send the request once (by submitting the search), remembers it (address,
headers, body) and then sends the same request again with another page number, keyword, city or filter. The request goes out
from the page itself, so it works in a background tab (the pages load nothing by scrolling when they are not visible).
"""

from __future__ import annotations

import json
from typing import Any, Callable

HOOK_ATTEMPTS = 16                       # x 0.5 s: how long to wait for the page to send its request


def js_hook(marker: str) -> str:
    """Script that remembers the next ``XMLHttpRequest`` whose address contains ``marker`` in ``window.__zpReq``."""
    return r"""
(() => {
  const marker = __MARKER__;
  window.__zpReq = null;
  if (window.__zpHooked === marker) return 'rearmed';
  window.__zpHooked = marker;
  const open = XMLHttpRequest.prototype.open, send = XMLHttpRequest.prototype.send, header = XMLHttpRequest.prototype.setRequestHeader;
  XMLHttpRequest.prototype.open = function (m, u) { this.__u = u; this.__h = {}; return open.apply(this, arguments); };
  XMLHttpRequest.prototype.setRequestHeader = function (k, v) { if (this.__h) this.__h[k] = v; return header.apply(this, arguments); };
  XMLHttpRequest.prototype.send = function (body) {
    if (String(this.__u).includes(window.__zpHooked)) window.__zpReq = {u: String(this.__u), h: this.__h, body: String(body)};
    return send.apply(this, arguments);
  };
  return 'hooked';
})()
""".replace("__MARKER__", json.dumps(marker))


JS_READ_TEMPLATE = "JSON.stringify(window.__zpReq || null)"

JS_REPLAY = r"""
(async () => {
  const req = window.__zpReq;
  if (!req) return JSON.stringify({error: 'no_template'});
  const body = Object.assign(JSON.parse(req.body), __OVERRIDES__);
  try {
    const response = await fetch(req.u, {method: 'POST', headers: req.h, body: JSON.stringify(body), credentials: 'include'});
    const text = await response.text();
    return JSON.stringify({status: response.status, body: text});
  } catch (e) {
    return JSON.stringify({error: String(e)});
  }
})()
"""


def valid_template(raw: Any) -> bool:
    """Whether ``raw`` (the answer to JS_READ_TEMPLATE) is a remembered request."""
    try:
        template = json.loads(raw) if isinstance(raw, str) else raw
    except (TypeError, ValueError):
        return False
    return isinstance(template, dict) and bool(template.get("u")) and isinstance(template.get("body"), str)


def capture_template(
    evaluate: Callable[[str, str], Any], target_id: str, marker: str, trigger: str, sleep: Callable[[float], None],
    attempts: int = HOOK_ATTEMPTS,
) -> bool:
    """Arm the hook, run ``trigger`` (a script that makes the page send the request) and wait until the request is
    remembered. False when it never was."""
    evaluate(target_id, js_hook(marker))
    evaluate(target_id, trigger)
    for _ in range(max(1, attempts)):
        if valid_template(evaluate(target_id, JS_READ_TEMPLATE)):
            return True
        sleep(0.5)
    return False


def replay(evaluate: Callable[[str, str], Any], target_id: str, overrides: dict[str, Any]) -> dict[str, Any]:
    """Send the remembered request again with ``overrides`` in its body. Returns ``{"status": int, "data": dict}`` or
    ``{"error": str}``."""
    script = JS_REPLAY.replace("__OVERRIDES__", json.dumps(overrides, ensure_ascii=False))
    raw = evaluate(target_id, script)
    try:
        wrapper = json.loads(raw) if isinstance(raw, str) else raw
    except json.JSONDecodeError:
        return {"error": "返回的不是 JSON"}
    if not isinstance(wrapper, dict):
        return {"error": "无返回"}
    if wrapper.get("error"):
        return {"error": str(wrapper["error"])}
    try:
        payload = json.loads(str(wrapper.get("body") or ""))
    except json.JSONDecodeError:
        return {"status": int(wrapper.get("status") or 0), "error": "返回的不是 JSON"}
    data = payload.get("data") if isinstance(payload, dict) else None
    return {"status": int(wrapper.get("status") or 0), "data": data if isinstance(data, dict) else {}}
