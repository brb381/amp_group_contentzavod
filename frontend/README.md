# AMP Content Factory Web

React/Vite-интерфейс для существующего FastAPI backend.

## Локальный запуск

Backend:

```powershell
cd backend
Copy-Item .env.example .env
docker compose up -d postgres redis minio minio-init migrate api
```

Frontend:

```powershell
cd frontend
npm.cmd install
npm.cmd run dev
```

Открыть: http://127.0.0.1:5173/

Vite проксирует `/api` и `/health` на `http://127.0.0.1:8000`. Другой адрес можно передать через `VITE_API_PROXY_TARGET`.

## Проверки

```powershell
npm.cmd run build

$env:AMP_E2E_EMAIL='admin@example.com'
$env:AMP_E2E_PASSWORD='local-password'
npm.cmd run smoke:e2e
```

Smoke-тест использует установленный системный Chrome и проверяет вход, cookie-сессию, дашборд и экран выгрузок.

## Подключённые контуры

- авторизация, восстановление и завершение cookie-сессии;
- CSRF-заголовок для изменяющих запросов;
- ролевое меню для блогера, модератора, менеджера, финансов, аналитика и администратора;
- дашборды блогера и сотрудников;
- профили, модерация публикаций, показания, выплаты, выгрузки, обращения и аналитика;
- скачивание готовых выгрузок;
- состояния загрузки, пустого ответа, потери сессии и недоступного backend.