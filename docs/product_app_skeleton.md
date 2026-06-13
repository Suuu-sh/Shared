# Products app skeleton

このディレクトリ配下で今後作るアプリは、Ohey / Talllk / FailBase の共通骨格に合わせる。

## 1. Repository layout

基本は product ごとに以下を置く。

```text
<Product>/
  Backend/        # Go API. 必須
  Frontend/       # Next.js Web. Web がある product のみ
  Mobile/         # Flutter app. Mobile がある product のみ
  AGENTS.md       # product 固有の運用差分があれば置く
```

Backend / Frontend / Mobile は、可能ならそれぞれ独立 Git repository にする。単一 repo にする場合もディレクトリ境界は同じにする。

## 2. Backend standard

### Runtime / libraries

- Language: Go 1.25.0
- HTTP: standard library `net/http` + `http.ServeMux`
- DB: Neon PostgreSQL
- DB driver: `github.com/jackc/pgx/v5`
- ORM: 使用しない
- Web framework: Gin/Echo/Fiber などは使用しない
- Auth: Clerk JWT / JWKS verification for auth-enabled products; FailBase stays unauthenticated
- Mail: Resend
- Storage: Cloudflare R2 as the standard object storage
- Error monitoring: Sentry when needed

### Backend tree

```text
Backend/
  cmd/
    api/
      main.go
  internal/
    config/
      config.go
      env.go                 # env key constants; appが小さければ省略可
    contracts/               # API path / shared constants; appが小さければ省略可
    features/
      <feature>/
        domain.go
        repository.go
        postgres_repository.go
        usecase.go
        *_test.go
    httpapi/
      router.go
      request.go
      response.go            # request.go に集約しても可
      auth_verifier.go       # Clerk JWT verification が必要な app
      middleware.go
      <feature>.go
      *_test.go
    postgres/
      db.go
  db/ or sql/
    migrations/
  docs/
  .env.example
  Dockerfile / render.yaml
  go.mod
```

### Backend entrypoint

`cmd/api/main.go` に統一する。

- `config.Load()` で env を読む
- `postgres.Open(context.Background(), postgres.Config{DatabaseURL, MaxConns})`
- `httpapi.NewRouter(httpapi.Dependencies{Config, Logger, Postgres, ...})`
- `http.Server` に timeout を明示する
- build command は `go build -o bin/server ./cmd/api`

### Backend config keys

全アプリでキー名を揃える。

```env
APP_ENV=development|staging|production
PORT=8080
DATA_STORE=neon
DATABASE_URL=
DATABASE_MAX_CONNS=10
AUTH_PROVIDER=clerk
CLERK_ISSUER=
CLERK_JWKS_URL=
CLERK_AUDIENCE=
CLERK_SECRET_KEY=
ALLOWED_ORIGINS=*
SENTRY_DSN=
SENTRY_TRACES_SAMPLE_RATE=0.1
RESEND_API_KEY=
RESEND_FROM_EMAIL=
RESEND_REPLY_TO_EMAIL=
```

App 固有で追加してよいキー例:

```env
R2_ACCOUNT_ID=
R2_ACCESS_KEY_ID=
R2_SECRET_ACCESS_KEY=
R2_BUCKET=
R2_PUBLIC_URL=
OPENAI_API_KEY=
OPENAI_BASE_URL=https://api.openai.com/v1
OPENAI_MODEL=gpt-4.1-mini
FCM_SERVICE_ACCOUNT_JSON=
TURNSTILE_SECRET_KEY=
```

### Backend dependency direction

```text
cmd/api
  -> internal/config
  -> internal/postgres
  -> internal/httpapi
      -> internal/features/<feature>
          -> internal/postgres only via pgxpool in postgres_repository.go
```

Rules:

- `features` must not import `httpapi`
- `features` must not import `net/http` except external API clients that are part of that feature
- `features` must not import framework/router code
- `httpapi` owns request parsing, auth context, JSON response, CORS, status mapping
- `postgres_repository.go` owns SQL
- `usecase.go` owns business rules
- `domain.go` owns DTO/domain structs
- `repository.go` owns interfaces

## 3. Auth standard

### Provider

- Clerk を標準にする
- Backend は Clerk session JWT を JWKS で検証する
- Backend は password login / local auth session を持たない
- User create / resolve は Clerk `sub` と email を基準に app-local users table へ同期する

### Frontend / Mobile client env

Frontend:

```env
NEXT_PUBLIC_API_BASE_URL=https://dev-<product>-backend.onrender.com
NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY=
NEXT_PUBLIC_CLERK_SIGN_IN_URL=/login
NEXT_PUBLIC_CLERK_SIGN_UP_URL=/login?mode=signup
NEXT_PUBLIC_SENTRY_DSN=
NEXT_PUBLIC_SENTRY_TRACES_SAMPLE_RATE=0.1
```

Mobile:

```env
APP_ENV=dev
API_BASE_URL=https://dev-<product>-backend.onrender.com/api
CLERK_PUBLISHABLE_KEY=
SENTRY_DSN=
SENTRY_TRACES_SAMPLE_RATE=0.1
REVENUECAT_IOS_API_KEY=
REVENUECAT_ANDROID_API_KEY=
ADMOB_IOS_BANNER_AD_UNIT_ID=
ADMOB_ANDROID_BANNER_AD_UNIT_ID=
```

Ohey など既存 app 固有 prefix がある場合も、新規 app は上記の汎用キーを優先する。

## 4. Database standard

- Neon PostgreSQL を標準にする
- Runtime は pooled connection string を使う
- Migration / dump は direct connection string を使う
- Runtime DB access は `pgxpool`
- `SELECT *` は避け、必要列を明示する
- `DATABASE_MAX_CONNS` を env で制御する
- Migration は SQL ファイルで管理する

Recommended migration layout:

```text
Backend/db/migrations/
  202606130001_create_users.sql
  202606130002_create_<feature>.sql
```

## 5. Mail standard

- Transactional mail は Resend
- Product ごとに verified domain の `noreply@<domain>` を使う
- Reply-To は support mailbox に寄せる
- Clerk custom email delivery を使う場合は `/webhooks/clerk/email` を標準 endpoint にする

Backend env:

```env
RESEND_API_KEY=
RESEND_FROM_EMAIL=<Product> <noreply@product-domain>
RESEND_REPLY_TO_EMAIL=support@product-domain
CLERK_WEBHOOK_SECRET=
```

## 6. Frontend standard

### Runtime / libraries

- Next.js App Router
- TypeScript strict
- Auth: Clerk Next SDK
- API calls: one typed API client layer
- Hosting: Cloudflare Pages / OpenNext or Vercel; productごとに決めるが env names は合わせる

### Frontend tree

```text
Frontend/
  app/
  src/
    app/ or routes/          # Next app routerをapp直下に置く場合は省略
    components/
    features/
      <feature>/
    lib/
      api/
        client.ts
        types.ts
      auth/
      env.ts
    styles/
  public/
  scripts/
  .env.example
  package.json
  tsconfig.json
```

Rules:

- Backend URL は `NEXT_PUBLIC_API_BASE_URL`
- Clerk publishable key は `NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY`
- Server-only secrets は `NEXT_PUBLIC_` を付けない
- API path strings は `lib/api` に集約する

## 7. Mobile standard

### Runtime / libraries

- Flutter
- Auth: Clerk Flutter/Auth SDK
- API calls: `services/api_service.dart` or feature repositories; app内で一箇所に集約
- Secrets: `.env.local` / dart-define. Repository には `.env.example` のみ
- RevenueCat / AdMob / Sentry は必要な app だけ使うがキー名は統一する

### Mobile tree

```text
Mobile/
  lib/
    main.dart
    app_dependencies.dart
    config/
    services/
      api_service.dart
      auth_service.dart
      auth_storage.dart
    features/
      <feature>/
        data/
        domain/
        presentation/
    screens/ or presentation/
    widgets/
  assets/
  test/
  integration_test/
  scripts/
  .env.example
  pubspec.yaml
```

Rules:

- API base URL は `API_BASE_URL`
- Clerk publishable key は `CLERK_PUBLISHABLE_KEY`
- Localhost を mobile 実機/Simulator の標準にしない。dev backend は Render などの共有 dev URL を使う
- Auth token retrieval は一箇所に集約する

## 8. CI standard

Backend:

```text
go test ./...
golangci-lint run --timeout=5m
go build -o bin/server ./cmd/api
```

Frontend:

```text
npm ci
npm run lint
npm run build
```

Mobile:

```text
flutter pub get
flutter analyze
flutter test
```

## 9. Current alignment snapshot

- Ohey Backend: standard source of truth
- Talllk Backend: aligned to `cmd/api`, `internal/httpapi`, `internal/postgres`, `internal/features`, Clerk + Neon + Resend/R2
- FailBase Backend: aligned to `cmd/api`, `internal/httpapi`, `internal/postgres`, `internal/features`, Neon + Resend; no auth by default; do not add Clerk until the product needs login
- Talllk / Ohey Mobile: Flutter + Clerk + API service layer
- Talllk / FailBase Frontend: Next.js; new work should standardize env names and Clerk integration where auth is required
