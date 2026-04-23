# Shared GitHub Actions Foundation

Monee と Talllk で使っている GitHub Actions / helper scripts を 1 か所に寄せるための共通基盤リポジトリです。

## 目的

- repo ごとに重複している CI / CD / automation の処理を共通化する
- 各プロダクト repo では **trigger / concurrency / environment 宣言** だけを薄く残す
- 重い shell / Ruby / Python ロジックはこの repo 側の composite action に集約する
- Monee / Talllk で改善した automation を横展開しやすくする

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

### Go backend

- `actions/go-test`
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
examples/  各プロダクト repo から呼び出す薄い workflow 例
scripts/   action から呼び出す共通スクリプト
```

## 使い方

各プロダクト repo の workflow は、通常どおり trigger を持たせたうえで、この repo の action を参照します。

```yaml
steps:
  - uses: actions/checkout@v5
  - uses: <OWNER>/<SHARED_REPO>/actions/go-test@main
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
- reusable workflow より composite action を優先しています。各 repo ごとに event / job 名 / required check 名を維持しやすいためです。

## サンプル

- `/Users/yota/Projects/Shared/examples/monee/testflight-deploy.yml`
- `/Users/yota/Projects/Shared/examples/monee/testflight-distribute-existing-build.yml`
- `/Users/yota/Projects/Shared/examples/talllk/backend-ci.yml`
- `/Users/yota/Projects/Shared/examples/talllk/backend-deploy.yml`
- `/Users/yota/Projects/Shared/examples/talllk/backend-sentry-triage.yml`
- `/Users/yota/Projects/Shared/examples/talllk/mobile-ship-criteria.yml`
