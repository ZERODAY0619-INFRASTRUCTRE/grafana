# Grafana + Prometheus

Prometheus가 ICPM/PCPM 메트릭을 수집하고 Grafana가 이를 기본 데이터 소스로
사용하는 Docker Compose 구성입니다. 로그인은
`.env`의 `GRAFANA_OAUTH_BASE_URL`로 지정한 authentik OIDC로 연결됩니다.

수집 대상은 다음과 같습니다.

- ICPM: `.env`의 `ICPM_METRICS_TARGET` (`host:port`)
- PCPM: `.env`의 `PCPM_METRICS_TARGET` (`host:port`)

## authentik 설정

authentik에서 OAuth2/OpenID Provider와 Application을 만들고 Strict Redirect URI를 다음으로 등록합니다.

```text
<GRAFANA_PUBLIC_URL>login/generic_oauth
```

예를 들어 공개 주소가 `https://grafana.example.com/`이면 Redirect URI는
`https://grafana.example.com/login/generic_oauth`입니다. 발급된 Client ID와 Client Secret을 `.env`에 넣습니다.

## 실행

```bash
cp .env.example .env
chmod 600 .env
${EDITOR:-vi} .env
python3 scripts/start-telemetry.py
```

두 대상은 같은 종류의 메트릭이므로 Prometheus의 공통
`job="caddy_proxy_manager"`로 수집하고, 다음 타깃 라벨로 구분합니다.

| 대상 | `cpm_instance` | `instance` |
| --- | --- | --- |
| ICPM | `icpm` | `ICPM` |
| PCPM | `pcpm` | `PCPM` |

Grafana의 Explore 화면에서 아래 PromQL 결과가 각각 `1`이면 수집이 정상입니다.

```promql
up{job="caddy_proxy_manager", cpm_instance="icpm"}
up{job="caddy_proxy_manager", cpm_instance="pcpm"}
```

두 대상을 한 번에 확인할 때는 아래처럼 조회하고 Grafana 범례를
`{{instance}}`로 지정합니다.

```promql
up{job="caddy_proxy_manager"}
```

기존의 `job="icpm"` 또는 `job="pcpm"` 필터를 사용하는 대시보드와 알림은
각각 `cpm_instance="icpm"`, `cpm_instance="pcpm"` 필터로 변경해야 합니다.

## Grafana 대시보드

Grafana가 시작되면 **Caddy Proxy Manager** 폴더에
**Caddy Proxy Manager / ICPM & PCPM** 대시보드가 자동으로 생성됩니다.
상단의 **CPM Instance** 변수로 전체, ICPM 또는 PCPM을 선택할 수 있습니다.
대시보드는 수집 상태와 가용성뿐 아니라 Caddy 구성 reload, reverse proxy
upstream health, Admin API 요청, 프로세스 CPU·메모리·네트워크·FD, Go
runtime·heap·GC와 metrics handler 지표를 30초마다 갱신합니다. 하단의
**전체 Raw Metric Inventory**에서는 선택한 인스턴스가 노출하는 모든 원시
시계열과 라벨의 현재값도 확인할 수 있습니다.

Prometheus 컨테이너도 `.env`에 지정한 exporter 이름을 DNS로 해석하고 접근할 수 있어야 합니다.
대상이 Tailscale MagicDNS 이름인 경우 Docker 호스트의 DNS 설정이 컨테이너에
전달되는지 확인해야 합니다.

컨테이너 포트는 기본적으로 `127.0.0.1:3000`에만 게시됩니다. 외부 reverse proxy는 이 주소로 전달합니다.
일반 접속은 authentik으로 자동 이동합니다. SSO 장애 시 로컬 로그인 화면은
`<GRAFANA_PUBLIC_URL>login?disableAutoLogin=true`로 엽니다.

## 개인 PC의 Codex 자동 연결 (Tailscale)

서버 수집 구성을 한 번 실행한 뒤 개인 PC에서 연결 스크립트를 실행합니다.
PC 스크립트는 `tailscale status --json`에 나타나는 기기의 Tailscale IPv4만
대상으로 TCP 14318의 서비스 식별 응답과 Collector 상태를 확인합니다.
서버 주소나 OpenAI API 키를 입력할 필요가 없습니다. PC에 프로그램을 원격
설치하는 기능은 아니므로 **각 PC에서 한 번 실행**해야 합니다.

```text
개인 PC Codex → Tailscale → 수집 게이트웨이 → OpenTelemetry Collector
                                              ├─ Prometheus (메트릭)
                                              ├─ Loki (로그, 7일)
                                              └─ Tempo (트레이스, 7일)
```

### 1. Grafana 서버에서 실행

기존 `.env`를 설정한 상태에서, Linux Docker 호스트의 Tailscale이 로그인되어
있어야 합니다. 서버 스크립트는 Python 3.11 이상에서 외부 패키지 없이 동작합니다.

```bash
python3 scripts/start-telemetry.py
```

이 명령은 서버의 Tailscale IP를 자동으로 구해 `compose.yml`을 적용합니다.
`compose.yml`은 `include`로 `compose.telemetry.yml`을 자동으로 불러옵니다
(Docker Compose 2.20.0 이상 필요).
수집 게이트웨이는 **해당 Tailscale IP의 14318 포트에만** 바인딩합니다.
HTTP는 Tailscale 암호화 터널 안에서 전달됩니다. Tailscale grants/ACL에서
허용된 개인 PC가 서버의 `tcp:14318`에 접근할 수 있어야 합니다. 이 포트에는
별도 앱 인증이 없으므로 수집 권한은 Tailscale 정책으로 제한합니다.
Loki·Tempo·Collector의 조회/관리 포트는 호스트에 게시하지 않습니다.

설정 검증만 하려면 `python3 scripts/start-telemetry.py --check`를 사용합니다.
서버 재부팅 후에는 Docker의 restart 정책이 적용됩니다. Tailscale IP가
변경되거나 구성을 업데이트하면 서버 스크립트를 다시 실행하세요.
직접 실행하려면 `.env`에 서버의 `OTEL_TAILSCALE_IP`를 설정한 뒤
`python3 scripts/generate-targets.py`와 `docker compose up -d`를 실행하세요.
일반 Compose 명령에도 수집 구성이 자동으로 포함됩니다.

### 2. 개인 PC에서 실행

이 저장소를 PC에도 내려받고 저장소 디렉터리에서 실행합니다. Python 3.11+
및 로그인된 Tailscale이 필요합니다. Codex가 실행되는 사용자 계정과 환경에서
실행하세요. WSL Codex는 WSL 안의 설정 파일을 사용하므로 해당 환경에서
실행하거나 `--config`로 실제 설정 경로를 지정해야 합니다.

Windows PowerShell:

```powershell
py -3 -m pip install -r scripts/requirements.txt
py -3 scripts/connect-codex.py
```

macOS / Linux:

```bash
python3 -m venv .venv-telemetry
.venv-telemetry/bin/python -m pip install -r scripts/requirements.txt
.venv-telemetry/bin/python scripts/connect-codex.py
```

이미 uv를 사용 중이라면 모든 OS에서 `uv run scripts/connect-codex.py`로
의존성 설치와 실행을 함께 할 수 있습니다. `scripts/tailnet.py`도 필요하므로
스크립트 한 파일만 복사하지 말고 저장소 또는 scripts 디렉터리를 가져오세요.

스크립트는 다음을 자동 처리합니다.

- 서버 1대가 발견되면 현재 PC의 `CODEX_HOME/config.toml` 또는 기본
  `~/.codex/config.toml`에 로그·메트릭·트레이스 OTLP HTTP 주소를 설정합니다.
- 모델, MCP, 프로필 등 다른 설정과 주석을 보존합니다. 기존 OTel 전송 대상은
  새 서버로 변경하며, 원본은 같은 디렉터리의 `config.toml.backup-*`에 백업합니다.
- `log_user_prompt = false`로 설정합니다. 도구 출력·경로 등 다른 이벤트 정보는
  로그에 포함될 수 있습니다.
- 서버가 없거나 여러 대이면 파일을 변경하지 않습니다. 재실행해도 중복 설정이나
  불필요한 백업이 생기지 않습니다.

Codex를 완전히 종료하고 다시 실행한 뒤 작업하면 **AI Telemetry →
Codex / Personal PC** 대시보드에 데이터가 표시됩니다. 과거 기록은 소급 수집하지
않습니다. 사용 중인 Codex 버전이 `otel.metrics_exporter`와 `otel.trace_exporter`를
지원해야 하며, 조직의 관리 정책이나 프로젝트 설정이 사용자 설정을 덮어쓰는지도
확인하세요. HTTP 프록시를 사용하는 PC에서는 Codex 프로세스의 `NO_PROXY`에
발견된 서버 IP를 포함해야 Tailscale로 직접 전송됩니다.

### 선택 및 진단

아래 명령의 `python3`는 Windows에서는 `py -3`, 가상환경에서는 해당 Python
실행 경로로 바꿔 사용하세요.

```bash
python3 scripts/connect-codex.py --dry-run
python3 scripts/connect-codex.py --server grafana-server
python3 scripts/connect-codex.py --config /path/to/codex/config.toml
```

자동 검색은 한 번에 최대 256대, 동시 8개 연결로 동작하며 기기마다 고정된 서비스
경로만 확인합니다. 그보다 큰 tailnet은 `--server`로 대상을 지정하세요.
한 번 연결된 뒤에는 Codex가 저장된 Tailscale IP로 전송하므로 매번 검색할 필요가
없습니다. 서버 IP가 바뀐 경우 PC에서도 스크립트를 다시 실행합니다.

대시보드의 Collector UP은 Prometheus 수집 상태입니다. 실제 PC 수신 여부는
Codex 원시 메트릭 및 이벤트 패널로 확인하고, 트레이스는 Explore의 **AI Traces**
데이터 소스에서 조회하세요. 요청 수는 Prometheus `increase` 기반 추정값이므로
첫 샘플 이전 요청과 짧은 세션은 집계에서 빠질 수 있습니다. Collector의 큐와
delta 누적 상태는 메모리에 있으므로 재시작·장시간 전송 장애 중 데이터가 유실될
수 있습니다. 과금 정산용 집계로 사용하지 않습니다.

원상 복구는 Codex를 종료한 뒤 원하는 `config.toml.backup-*`를 `config.toml`로
복사하면 됩니다. 이후 변경한 설정이 있다면 백업 전체 복사 대신 `[otel]`만
복구하세요. 수집 데이터 볼륨은 자동으로 삭제하지 않습니다.

일반 OpenAI 앱 연동은 앱 자체의 계측 작업이 별도로 필요합니다. 이번 자동 연결은
Codex의 내장 OTel 전송을 설정하며, 다른 앱의 API 호출을 자동으로 가로채지 않습니다.

참고: [Grafana Codex 연동](https://grafana.com/docs/grafana-cloud/observe-and-act/monitor-infrastructure/integrations/integration-reference/integration-openai-codex/),
[Codex OTel 설정](https://learn.chatgpt.com/docs/config-file/config-advanced#observability-and-telemetry).
이 저장소의 대시보드는 자체 호스팅용으로 작성한 것이며 Cloud 대시보드 복사본은 아닙니다.

## 공개 저장소에 올리기 전

실제 비밀번호, OAuth Client ID/Secret은 `.env`에만 보관하고, 배포 주소의 원본 설정도 `.env`에서 관리합니다.
`.env.example`에는 예시 주소와 빈 크레덴셜만 유지합니다. OAuth 기본 URL은
마지막 `/` 없이 입력합니다. 기존 `.env`가 있으면 덮어쓰지 말고 새 키를 추가하세요.
수집 대상을 바꾼 뒤에는 `python3 scripts/generate-targets.py`를 다시 실행합니다.
생성된 `prometheus/cpm-targets/cpm.json`도 Git에서 제외됩니다.
telemetry 실행 스크립트는 타깃 생성도 자동 수행합니다.

`docker compose config`의 전체 출력에는 비밀값이 포함될 수 있으므로 공유하지 말고,
검증에는 `docker compose config --quiet`를 사용합니다. `.env`와 백업, 실제
메트릭·로그 데이터는 커밋하지 마세요. `.gitignore`는 이미 추적 중인 파일이나
과거 커밋을 지우지 않습니다. 실제 Git 저장소에서 `git ls-files`로 추적 여부와
히스토리 secret scan을 별도로 확인하고, 과거 노출된 키는 폐기·재발급해야 합니다.
