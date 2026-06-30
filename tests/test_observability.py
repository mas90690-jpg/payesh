# tests/test_observability.py
import json
import logging
from core.obs_server import render_prometheus
from core.logger import JsonFormatter


def test_render_prometheus_format():
    counts = {"messages": 100, "leads": 40, "hot": 5,
              "by_platform": {"telegram": 20, "bale": 20}}
    text = render_prometheus(counts, {"cpu": 12.3, "ram": 45.6, "uptime": 99},
                             {"telegram": True, "bale": False})
    assert "app_messages_total 100" in text
    assert "app_leads_total 40" in text
    assert 'app_leads_by_platform{platform="telegram"} 20' in text
    assert 'collector_up{platform="bale"} 0' in text
    assert 'collector_up{platform="telegram"} 1' in text
    assert "system_cpu_percent 12.3" in text
    # هر متریک باید HELP/TYPE داشته باشد (فرمت استاندارد Prometheus)
    assert "# TYPE app_messages_total counter" in text


def test_json_log_line_is_valid_json():
    rec = logging.LogRecord("srooyesh", logging.INFO, __file__, 1, "hello world", None, None)
    rec.context = {"platform": "telegram", "n": 3}
    line = JsonFormatter().format(rec)
    obj = json.loads(line)              # باید JSON معتبر باشد
    assert obj["level"] == "INFO"
    assert obj["msg"] == "hello world"
    assert obj["context"] == {"platform": "telegram", "n": 3}
    assert "ts" in obj                  # ISO-8601 timestamp
