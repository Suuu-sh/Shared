# Shared GitHub Actions Foundation

Monee と Talllk で使っている GitHub Actions / helper scripts を 1 か所に寄せるための共通基盤リポジトリです。

## 目的

- repo ごとに重複している CI / CD / automation の処理を共通化する
- 各プロダクト repo では **trigger / concurrency / environment 宣言** だけを薄く残す
- 重い shell / Ruby / Python ロジックはこの repo 側の composite action に集約する
- Monee / Talllk で改善した automation を横展開しやすくする

## Shared Workers

### `workers/shared-api-proxy`

Cloudflare Worker 1台で複数プロダクトの API reverse proxy を管理します。

| Host | Origin | Purpose |
| --- | --- | --- |
| `api.failbase.app` | `https://failbase-backend.onrender.com` | FailBase production API |
| `api.oheyapp.com` | `https://ohey-backend.onrender.com` | Ohey production API |
| `api.talllk.net` | `https://talllk-backend.onrender.com` | Talllk production API |

```bash
cd workers/shared-api-proxy
npm install
npm run check
wrangler secret put OHEY_ORIGIN_VERIFY_SECRET
wrangler secret put TALLLK_ORIGIN_VERIFY_SECRET
npm run deploy
```

`*_ORIGIN_VERIFY_SECRET` は Worker から対象 origin backend へ `X-Origin-Verify`
として渡す product 別シークレットです。各 backend 側には同じ値を
`ORIGIN_VERIFY_SECRET` として設定し、Render/Fly の直 URL からの
production API 直叩きを防ぎます。

## 収録している共通 action

### iOS / TestFlight

- `actions/testflight-deploy`
  - Xcode 選択
  - App Store Connect API key 作成
  - 証明書 / Provisioning Profile のインストール
  - archive / export / TestFlight upload
  - 内部テスター配布の自動化
- `actions/testflight-distribute-existing-build`
  - 既存 TestFlight build を内部テスターへ再配布


### Supabase / PostgreSQL migrations

- `.github/workflows/supabase-postgres-migrate.yml`
  - caller repository の migration SQL を検証します
  - `push` かつ `deploy-ref` に一致する場合だけ production DB に適用します
  - 実行済み migration は `public.app_schema_migrations` で管理します
  - connection string は caller repository の Actions secret として渡します

Caller example:

```yaml
jobs:
  migrate-production-db:
    uses: Suuu-sh/Shared/.github/workflows/supabase-postgres-migrate.yml@main
    with:
      migrations-dir: supabase/migrations
      deploy-ref: refs/heads/main
    secrets:
      database_url: ${{ secrets.SUPABASE_PRODUCTION_DATABASE_URL }}
```

### Go backend

- `actions/go-test`

### Render deploy reusable workflow

- `.github/workflows/render-deploy.yml`
  - caller repository を checkout します
  - `actions/setup-go` で Go をセットアップします
  - `test-command` を実行します
  - `push` かつ `deploy-ref` に一致する場合だけ Render Deploy Hook を呼びます

Caller example:

```yaml
jobs:
  render:
    uses: Suuu-sh/Shared/.github/workflows/render-deploy.yml@main
    with:
      go-version-file: go.mod
      test-command: go test ./...
      deploy-ref: refs/heads/main
    secrets:
      render_deploy_hook_url: ${{ secrets.RENDER_DEPLOY_HOOK_URL }}
```

- `actions/go-lint`
- `actions/go-build`
- `actions/postgres-migrate`
- `actions/sentry-resolve`
- `actions/sentry-triage`

### Flutter mobile

- `actions/flutter-analyze`
- `actions/flutter-test`

## ディレクトリ構成

```text
actions/   composite actions 本体
scripts/   action から呼び出す共通スクリプト
workers/   Cloudflare Workers など共有インフラコード
```

## 使い方

各プロダクト repo の workflow は、通常どおり trigger を持たせたうえで、この repo の action を参照します。

```yaml
steps:
  - uses: actions/checkout@v5
  - uses: <OWNER>/<SHARED_REPO>/actions/go-test@<COMMIT_SHA>
    with:
      working-directory: .
      go-version: '1.23'
```

## まず移行できる対象

### Monee

- `testflight-deploy.yml` → `actions/testflight-deploy`
- `testflight-distribute-existing-build.yml` → `actions/testflight-distribute-existing-build`
- `.github/scripts/ensure_testflight_internal_distribution.rb` → `scripts/ensure_testflight_internal_distribution.rb`

### Talllk Backend

- `ci.yml` → `actions/go-test` / `actions/go-lint` / `actions/go-build`
- `deploy.yml` の migration / sentry resolve 部分 → `actions/postgres-migrate` / `actions/sentry-resolve`
- `sentry-triage.yml` → `actions/sentry-triage`

### Talllk Mobile

- `ship-criteria.yml` → `actions/flutter-analyze` / `actions/flutter-test`

## 移行時メモ

- caller repo 側では `actions/checkout` を先に実行してください。
- private repo 間で使う場合は、shared repo 側の **Actions access policy** を caller repo から参照できるように設定してください。
- 呼び出し側では `@main` ではなく commit SHA pin を推奨します。
- reusable workflow より composite action を優先しています。各 repo ごとに event / job 名 / required check 名を維持しやすいためです。
