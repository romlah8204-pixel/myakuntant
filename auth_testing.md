# Liniar Auth Testing

Demo admin: `admin@liniar.id` / `Liniar123!` (role: admin)

Endpoints: `POST /api/auth/login`, `GET /api/auth/me`, `POST /api/auth/logout`.
Login sets an httpOnly `access_token` cookie. The dashboard and all workspace routes require authentication.