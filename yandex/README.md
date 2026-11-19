# Мгновенный бот в Yandex Cloud Functions

Функция `index.py` принимает события от MAX и обрабатывает их сразу:
- команды владельца в личке бота выполняются за 1–2 секунды;
- новый, изменённый или удалённый пост в канале сразу запускает обновление сайта на GitHub (сайт обновляется за 1–2 минуты).

Если функция сломается или её отключить, бот сам вернётся к работе через GitHub (раз в 5–15 минут).

## Настройка

1. **Токен GitHub:** GitHub → Settings → Developer settings → Personal access tokens → **Fine-grained tokens** → Generate new token.
   - Repository access: **Only select repositories** → `model3d-nagaevo`
   - Permissions → Repository: **Contents: Read and write**, **Actions: Read and write**
2. **Функция:** console.yandex.cloud → Cloud Functions → Создать функцию → среда **Python 3.12**.
   - Файл `index.py` — вставить код из этой папки, точка входа `index.handler`, таймаут **30 с**, память **128 МБ**.
   - Переменные окружения: `MAX_TOKEN`, `GITHUB_TOKEN`, `SECRET` (придумать: 20+ латинских букв и цифр).
   - Включить **«Публичная функция»**.
3. Открыть в браузере `<ссылка для вызова>?setup=<SECRET>`. Все пункты должны быть ✅.

Проверить: `?check=<SECRET>`. Отключить webhook (вернуться на GitHub): `?off=<SECRET>`.
