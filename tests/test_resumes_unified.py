import io
import json

from bosshunter.resumes import resume_path_for_job
from bosshunter.web import server


def _files(tmp_path):
    paths = {}
    for name in ("main.md", "agent.md", "java.md"):
        path = tmp_path / name
        path.write_text(f"# {name}\n内容", encoding="utf-8")
        paths[name] = str(path)
    return paths


def test_the_default_resume_can_have_a_direction_like_every_other_one(tmp_path):
    paths = _files(tmp_path)
    config = {"profile": {"resume_path": paths["main.md"], "resume_direction": "Java后端",
                          "resumes": [{"path": paths["agent.md"], "direction": "Agent"}]}}
    assert resume_path_for_job(config, {"title": "Agent开发工程师", "category": ""}) == paths["agent.md"]
    assert resume_path_for_job(config, {"title": "Java后端工程师"}) == paths["main.md"]          # the default matches its own direction
    assert resume_path_for_job(config, {"title": "产品经理"}) == paths["main.md"]                # nothing matches: the default is the fallback
    without_direction = {"profile": {"resume_path": paths["main.md"], "resumes": [{"path": paths["agent.md"], "direction": "Agent"}]}}
    assert resume_path_for_job(without_direction, {"title": "Java后端工程师"}) == paths["main.md"]


def _call(path, method="GET", body=None):
    raw = json.dumps(body).encode("utf-8") if body is not None else b""
    captured = {}
    environ = {
        "REMOTE_ADDR": "127.0.0.1", "REQUEST_METHOD": method, "PATH_INFO": path.split("?")[0], "QUERY_STRING": path.partition("?")[2],
        "SERVER_NAME": "127.0.0.1", "SERVER_PORT": "8686", "HTTP_HOST": "127.0.0.1:8686", "wsgi.version": (1, 0), "wsgi.url_scheme": "http",
        "wsgi.input": io.BytesIO(raw), "wsgi.errors": io.StringIO(), "wsgi.multithread": False, "wsgi.multiprocess": False, "wsgi.run_once": False,
    }
    if body is not None:
        environ["CONTENT_LENGTH"] = str(len(raw))
        environ["CONTENT_TYPE"] = "application/json"
    text = b"".join(c if isinstance(c, bytes) else c.encode("utf-8") for c in server.app(environ, lambda s, h, e=None: captured.update(status=s))).decode("utf-8")
    return captured["status"], json.loads(text)


def test_choosing_another_resume_as_default_swaps_it_with_the_old_one(tmp_path):
    from bosshunter.config import load_config, save_config

    server.set_base_dir(tmp_path)
    paths = _files(tmp_path)
    config = load_config(server.CONFIG_PATH)
    config["profile"].update(resume_path=paths["main.md"], resume_direction="Java后端", resumes=[{"path": paths["agent.md"], "direction": "Agent"}])
    save_config(config, server.CONFIG_PATH)

    status, _ = _call("/api/resumes", "PATCH", {"path": paths["main.md"], "direction": "后端开发"})
    assert status.startswith("200") and load_config(server.CONFIG_PATH)["profile"]["resume_direction"] == "后端开发"      # the default takes a direction too
    status, payload = _call("/api/resumes/default", "POST", {"path": paths["agent.md"]})
    assert status.startswith("200"), payload
    profile = load_config(server.CONFIG_PATH)["profile"]
    assert profile["resume_path"] == paths["agent.md"] and profile["resume_direction"] == "Agent"
    assert profile["resumes"] == [{"path": paths["main.md"], "direction": "后端开发"}]                                 # the old default is listed now
    status, payload = _call("/api/resumes/content?path=" + paths["agent.md"].replace("\\", "/"))
    assert status.startswith("200") and "agent.md" in payload["content"]
    assert _call("/api/resumes/content?path=" + str(tmp_path / "other.md").replace("\\", "/"))[0].startswith("404")
