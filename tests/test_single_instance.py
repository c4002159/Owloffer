import socket

import pytest

from bosshunter import single_instance as si


@pytest.fixture(autouse=True)
def _release():
    yield
    si.release()


def _free_port():
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


def test_a_second_start_on_the_same_data_is_refused_while_the_first_lives(tmp_path):
    lock = tmp_path / "server.lock"
    port = _free_port()
    si.acquire(lock, "127.0.0.1", port)
    assert lock.read_text(encoding="utf-8").strip().isdigit()                   # the owner is written down
    with pytest.raises(si.AlreadyRunning) as refused:
        second = open(lock, "a+", encoding="utf-8")                              # what a second process would do
        try:
            assert not si._lock(second)
            raise si.AlreadyRunning("已经有另一个服务器在使用这份数据")
        finally:
            second.close()
    assert "另一个服务器" in refused.value.detail


def test_the_lock_is_free_again_after_release(tmp_path):
    lock = tmp_path / "server.lock"
    port = _free_port()
    si.acquire(lock, "127.0.0.1", port)
    si.release()
    si.acquire(lock, "127.0.0.1", port)                                          # no AlreadyRunning


def test_a_port_that_already_answers_is_refused_even_without_the_lock(tmp_path):
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        listener.listen(1)
        port = listener.getsockname()[1]
        with pytest.raises(si.AlreadyRunning) as refused:
            si.acquire(tmp_path / "other.lock", "127.0.0.1", port)
        assert str(port) in refused.value.detail
        assert not (tmp_path / "other.lock").exists()                            # refused before touching the data folder


def test_a_second_process_cannot_hold_the_lock_at_the_same_time(tmp_path):
    import subprocess
    import sys

    lock = tmp_path / "server.lock"
    si.acquire(lock, "127.0.0.1", _free_port())
    code = (
        "import sys; sys.path.insert(0, r'" + str(__import__('pathlib').Path(si.__file__).parent.parent) + "');"
        "from bosshunter import single_instance as si;"
        "import pathlib\n"
        "try:\n"
        "    si.acquire(pathlib.Path(sys.argv[1]), '127.0.0.1', int(sys.argv[2]))\n"
        "    print('ACQUIRED')\n"
        "except si.AlreadyRunning as exc:\n"
        "    print('REFUSED', exc.detail)\n"
    )
    out = subprocess.run([sys.executable, "-X", "utf8", "-c", code, str(lock), str(_free_port())], capture_output=True, timeout=60)
    text = out.stdout.decode("utf-8", "replace") + out.stderr.decode("utf-8", "replace")
    assert "REFUSED" in text, text
