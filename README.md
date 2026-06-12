# Shared API Proxy

Cloudflare Worker for shared API reverse proxy routes across products.

## Routes

| Host | Origin | Purpose |
| --- | --- | --- |
| `api.failbase.app` | `https://failbase-backend.onrender.com` | FailBase production API |
| `api.oheyapp.com` | `https://ohey-backend.onrender.com` | Ohey production API |

## Commands

```bash
npm install
npm run check
npm run deploy
```

## Design

- Dispatch by incoming `Host`.
- Rewrite origin URL and `Host` header to the Render `*.onrender.com` host.
- Preserve path, query, method, headers, and body.
- Set `X-Forwarded-Host`, `X-Forwarded-Proto`, and `X-Proxy-Route` for origin-side diagnostics.
