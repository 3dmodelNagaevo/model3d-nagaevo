"""Мини-клиент Bot API MAX (только стандартная библиотека)."""
import json
import pathlib
import ssl
import urllib.error
import urllib.parse
import urllib.request

ROOT = pathlib.Path(__file__).resolve().parent.parent
CERTS = ROOT / ".certs"
API_HOSTS = ["https://platform-api2.max.ru", "https://platform-api.max.ru"]
UA = {"User-Agent": "model3d-catalog-sync"}


def log(*a):
    print(*a, flush=True)


class ApiError(Exception):
    pass


def _ssl_context():
    """Системные сертификаты + сертификаты Минцифры (нужны для platform-api2.max.ru)."""
    ctx = ssl.create_default_context()
    if CERTS.is_dir():
        for f in sorted(CERTS.glob("*.crt")):
            try:
                ctx.load_verify_locations(cafile=str(f))
            except Exception as e:
                log(f"  ! не удалось загрузить сертификат {f.name}: {e}")
    return ctx


CTX = _ssl_context()


def _request(method, path, params, token, body=None):
    query = urllib.parse.urlencode(params or {})
    data = json.dumps(body, ensure_ascii=False).encode("utf-8") if body is not None else None
    last_err = None
    for host in API_HOSTS:
        url = f"{host}{path}?{query}" if query else f"{host}{path}"
        headers = {"Authorization": token, **UA}
        if data is not None:
            headers["Content-Type"] = "application/json"
        req = urllib.request.Request(url, data=data, headers=headers, method=method)
        try:
            with urllib.request.urlopen(req, timeout=40, context=CTX) as r:
                raw = r.read().decode("utf-8")
                return json.loads(raw) if raw else {}
        except urllib.error.HTTPError as e:
            text = e.read().decode("utf-8", "replace")[:300]
            if e.code == 401:
                raise ApiError("MAX ответил 401: неверный или перевыпущенный токен. Обновите секрет MAX_TOKEN.")
            if e.code in (400, 403, 404):
                # это ответ самого API, другой домен не поможет
                raise ApiError(f"MAX ответил {e.code} на {method} {path}: {text}")
            last_err = f"{host}: HTTP {e.code} {text}"
        except Exception as e:  # SSL, DNS, таймаут — пробуем следующий домен
            last_err = f"{host}: {e}"
        log(f"  ! {last_err}")
    raise ApiError(f"Не удалось обратиться к API MAX. Последняя ошибка: {last_err}")


def get(path, params, token):
    return _request("GET", path, params, token)


def post(path, params, token, body):
    return _request("POST", path, params, token, body)


def patch(path, params, token, body):
    return _request("PATCH", path, params, token, body)


def download(url):
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=60, context=CTX) as r:
        data = r.read()
    if len(data) < 200:
        raise ValueError("слишком маленький файл")
    return data
