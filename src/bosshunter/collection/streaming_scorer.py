"""Score new postings while the collection is still running, instead of after it.

Collection saves a posting, hands its id to this worker, and goes on to the next page. The worker gathers the ids into small
batches and scores each batch with the same scoring code a separate scoring task uses. It never touches the collector's
database connection: scoring opens its own, and both sides write in short transactions (WAL mode, a generous busy timeout).

Whatever the worker could not score (it stopped, the AI paused, the review list was full) simply stays ``pending`` in the
database; the collection runs the ordinary scoring pass over the leftovers when it ends, so the final state is the same as
without this worker, only reached earlier.
"""

from __future__ import annotations

import threading
import time
from typing import Callable

BATCH_SIZE = 8
MAX_WAIT_SECONDS = 45.0               # a batch is not held longer than this for company
BACKOFF_SECONDS = 300.0               # after a failed or paused batch: leave scoring alone for a while


class StreamingScorer:
    def __init__(
        self,
        score_batch: Callable[[list[str]], int],
        *,
        stop_event: threading.Event | None = None,
        log: Callable[[str], None] | None = None,
        batch_size: int = BATCH_SIZE,
        max_wait: float = MAX_WAIT_SECONDS,
        backoff: float = BACKOFF_SECONDS,
        clock: Callable[[], float] = time.monotonic,
    ):
        """``score_batch(ids)`` scores them and returns how many of them are still unscored afterwards."""
        self.score_batch, self.stop_event, self.log = score_batch, stop_event, log or (lambda _message: None)
        self.batch_size, self.max_wait, self.backoff, self.clock = max(1, batch_size), max_wait, backoff, clock
        self._queue: list[tuple[float, str]] = []
        self._seen: set[str] = set()
        self._cond = threading.Condition()
        self._closed = False
        self._blocked_until = 0.0
        self._thread: threading.Thread | None = None
        self.stats = {"submitted": 0, "batches": 0, "errors": 0, "deferred_batches": 0, "left_over": 0}

    # ------------------------------------------------------------------ the collector's side
    def submit(self, job_id: str) -> None:
        job_id = str(job_id or "")
        with self._cond:
            if not job_id or job_id in self._seen or self._closed:
                return
            self._seen.add(job_id)
            self._queue.append((self.clock(), job_id))
            self.stats["submitted"] += 1
            self._cond.notify_all()

    def start(self) -> None:
        self._thread = threading.Thread(target=self._run, name="streaming-scorer", daemon=True)
        self._thread.start()

    def finish(self, timeout: float | None = None) -> dict[str, int]:
        """No more ids are coming: score what is queued (unless stopped or backing off) and stop the worker."""
        with self._cond:
            self._closed = True
            self._cond.notify_all()
        if self._thread is not None:
            self._thread.join(timeout)
        with self._cond:
            self.stats["left_over"] = len(self._queue)
        return dict(self.stats)

    # ------------------------------------------------------------------ the worker
    def _stopped(self) -> bool:
        return bool(self.stop_event is not None and self.stop_event.is_set())

    def _next_batch(self) -> list[str] | None:
        """Wait until a batch is due; None when the worker should end."""
        with self._cond:
            while True:
                if self._stopped():
                    return None
                now = self.clock()
                blocked = now < self._blocked_until
                if self._closed and (blocked or not self._queue):
                    return None                                   # closing while backing off: the final pass takes the rest
                if self._queue and not blocked:
                    oldest = self._queue[0][0]
                    if len(self._queue) >= self.batch_size or now - oldest >= self.max_wait or self._closed:
                        batch = [job_id for _t, job_id in self._queue[: self.batch_size]]
                        del self._queue[: self.batch_size]
                        return batch
                self._cond.wait(timeout=0.25)

    def _run(self) -> None:
        while True:
            batch = self._next_batch()
            if batch is None:
                return
            try:
                left = int(self.score_batch(batch))
            except Exception as exc:                              # one bad batch must not stop collecting
                self.stats["errors"] += 1
                self.log(f"边采边评：这一批评分出错（{type(exc).__name__}），先不再自动评分，采集结束后统一处理")
                self._blocked_until = self.clock() + self.backoff
                continue
            self.stats["batches"] += 1
            if left > 0:                                          # AI paused, review list full ...: do not hammer it
                self.stats["deferred_batches"] += 1
                self._blocked_until = self.clock() + self.backoff
                self.log(f"边采边评：这一批有 {left} 个没评上（评分暂停或待审查已满），采集结束后统一处理")
            else:
                self.log(f"边采边评：已评 {len(batch)} 个，队列里还有 {len(self._queue)} 个")
