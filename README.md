# itda-django — 실행세계를 단계별로 엿보는 Django 프로젝트

> 교육 프로젝트는 **itda-django**(이 저장소), 실행세계 도구면 패키지는 **django-itda**(`packages/django-itda/`, 같은 저장소의 uv workspace 멤버). 이름이 거울상이라 적어 둔다. 2026-09-06 까지의 이름은 hyve-django 였다.

> **"AI 직원을 고용한 점주의 가게"** — AI가 주문·환불을 *제안*하고, 가게의 법(불변식·전이 계약·격상 임계)이 *판정*하고, 점주(학생)가 admin 승인 큐에서 *확정*한다.

한 단계에 법 하나. 매 단계는 `준비 → 명령 → 의도적 실패 → 수정 → 검증 → 제출물` 순서로 진행한다.
설계 정본은 [docs/단계별-설계.md](docs/단계별-설계.md), 단계 색인은 [stages/README.md](stages/README.md),
켜진 법의 대장은 [RULES.md](RULES.md).

## 빠른 시작

필요한 것: Python 3.12+, [uv](https://docs.astral.sh/uv/), [just](https://just.systems).

```bash
just setup   # uv sync → migrate → seed_world
just run
```

자리가 둘이다. 창을 두 개로 나눠(일반 창 · 시크릿 창) 각각 로그인한다.

| 자리 | 주소 | 계정 |
|---|---|---|
| AI 직원 콘솔 (제안) | <http://127.0.0.1:8000/agent/> | `ai-staff` / `ai1234` |
| 점주 승인 큐 (확정) | <http://127.0.0.1:8000/admin/orders/refund/> | `owner` / `owner1234` |

시드 계정

| 계정 | 비밀번호 | 역할 |
|---|---|---|
| `owner` | `owner1234` | 점주. 그룹 `점주` — 환불 승인·거부. **superuser 아님** |
| `ai-staff` | `ai1234` | AI 직원 서비스 계정. 그룹 `AI직원` — 주문 접수·환불 제안까지 |
| `admin` | `admin1234` | superuser. 교육용 뒷문 |
| `alice`, `bob` | `pass1234` | 고객 |

## 자주 쓰는 명령

```bash
just test        # 전체 테스트
just test 00     # tests/stage_00_*.py 만
just reset-db    # db.sqlite3 삭제 후 migrate + seed_world
just stage 04    # stage-04-start 태그로 이동(그 단계의 시작 상태)
```

## 단계 태그 사용법

단계마다 태그가 두 개 있다.

- `stage-NN-start` — 그 단계가 시작되는 상태. 다음에 터질 결함이 심어져 있고,
  `tests/stage_NN_*.py`가 **실패한다**. 실패 자체가 "보장이 없다"의 증거다.
- `stage-NN-done` — 법이 켜진 상태. 같은 테스트가 통과한다.

```bash
just stage 04                              # 4단계 시작 상태로
just test 04                               # 실패를 눈으로 확인
git diff stage-04-start stage-04-done      # 정답 해설
git checkout main                          # 원위치
```

예외가 둘 있다.

- 0단계는 시작 상태가 빈 저장소이므로 `stage-00-start`가 없다. `stage-00-done`만 있다.
- **2·3단계는 애플리케이션 코드를 고치지 않는다**(해부·실측 단계). 앱 코드가 이전 단계와
  같으므로 `-start` 태그 없이 `stage-02-done` · `stage-03-done`만 있고, 그 테스트는
  이전 단계 완료 상태에서 이미 통과한다. 자세한 규약은 [stages/README.md](stages/README.md).

| # | 단계 | 태그 |
|---|---|---|
| 0 | 씨앗 | `stage-00-done` |
| 1 | 점주의 자리 | `stage-01-start` · `stage-01-done` |
| 2 | 귀속 | `stage-02-done` |
| 3 | 워밍업 | `stage-03-done` |

## 구조

```
config/     프로젝트 설정·URL
accounts/   User(AbstractUser) · seed_world 관리 명령(계정·그룹·권한)
shop/       Category · Product
orders/     Order · OrderItem · Refund · 환불 확정 API
agent/      AI 직원 콘솔 · 고정 시나리오 fixture
tests/      단계별 채점 테스트 (stage_NN_*.py)
stages/     단계별 학생용 지시서
docs/       설계 정본
```
