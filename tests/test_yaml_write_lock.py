import threading

import yaml

from bosshunter.config import _write_yaml_atomic


def test_many_threads_writing_the_same_file_never_fail_and_leave_valid_yaml(tmp_path):
    target = tmp_path / ".config.credentials.yaml"
    errors = []

    def write(number):
        try:
            for round_number in range(15):
                _write_yaml_atomic(target, {"writer": number, "round": round_number})
        except Exception as exc:                      # pragma: no cover - the failure this test guards against
            errors.append(exc)

    threads = [threading.Thread(target=write, args=(i,)) for i in range(6)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert not errors
    assert yaml.safe_load(target.read_text(encoding="utf-8"))["round"] == 14
    assert [p.name for p in tmp_path.iterdir()] == [target.name]      # no temp files left behind
