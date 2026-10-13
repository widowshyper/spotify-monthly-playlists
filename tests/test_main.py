import json
import logging

import config
import main


def _reset_logging():
    root = logging.getLogger()
    for h in list(root.handlers):
        if not type(h).__name__.startswith("LogCapture"):
            root.removeHandler(h)
            h.close()


def test_auto_without_client_id_exits_2_and_logs():
    try:
        assert main.main(["--auto"]) == 2
        text = config.log_path("auto.log").read_text(encoding="utf-8")
        assert "No Spotify Client ID configured" in text
    finally:
        _reset_logging()


def test_auto_without_login_exits_2():
    config.set_client_id("abc123")
    try:
        assert main.main(["--auto"]) == 2
        assert "Not logged in" in config.log_path("auto.log").read_text(encoding="utf-8")
    finally:
        _reset_logging()


def test_config_lives_in_app_dir_not_project(tmp_path):
    config.set_client_id("abc123")
    assert config.config_path().parent == config.app_dir()
    assert json.loads(config.config_path().read_text())["client_id"] == "abc123"
    assert config.get_client_id() == "abc123"
