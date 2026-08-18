# Jobs 운영 가이드

이 문서는 `WEB_COLLECTION` Worker를 웹 프로세스와 분리해 운영할 때 필요한
스케줄러 예시와 릴리즈 게이트를 정의한다. 아래 예시 중 **지속 Worker** 또는
**주기적 `run-due`** 하나만 선택한다. 둘을 동시에 실행하지 않는다.

## 공통 준비

```bash
cd /srv/nest
.venv/bin/python -m tools.jobs initialize
.venv/bin/python -m tools.jobs verify
.venv/bin/python -m tools.jobs health
```

환경 파일에는 `JOBS_ENABLED=true`, `JOBS_WEB_COLLECTION_ENABLED=true`와 DB
경로를 설정한다. `OPENAI_API_KEY`는 파일 권한이 제한된 서비스 환경 또는 secret
manager에서만 주입하고 명령행 인자로 전달하지 않는다.

## systemd 지속 Worker

`/etc/systemd/system/nest-jobs-worker.service` 예시:

```ini
[Unit]
Description=Nest Jobs Worker
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=nest
Group=nest
WorkingDirectory=/srv/nest
EnvironmentFile=/etc/nest/jobs.env
ExecStart=/srv/nest/.venv/bin/python -m tools.jobs worker --poll-seconds 30 --limit 50
Restart=on-failure
RestartSec=5
KillSignal=SIGTERM
TimeoutStopSec=240
NoNewPrivileges=true
PrivateTmp=true
ProtectSystem=strict
ProtectHome=true
ReadWritePaths=/srv/nest/instance
MemoryMax=2G
CPUQuota=200%
TasksMax=256

[Install]
WantedBy=multi-user.target
```

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now nest-jobs-worker.service
sudo systemctl status nest-jobs-worker.service
```

`MemoryMax`, `CPUQuota`, `TasksMax`는 시작 예시일 뿐이다. 실제 동시성·대상
페이지를 부하 시험한 뒤 더 낮은 값으로 조정하며, OOM/CPU 제한 상황에서 lease
recovery가 정상 동작하는지 확인한다.

## systemd timer로 `run-due`

지속 Worker 대신 1분마다 일회성 실행을 사용할 수 있다.

`/etc/systemd/system/nest-jobs-run-due.service`:

```ini
[Unit]
Description=Run due Nest Jobs once

[Service]
Type=oneshot
User=nest
Group=nest
WorkingDirectory=/srv/nest
EnvironmentFile=/etc/nest/jobs.env
ExecStart=/srv/nest/.venv/bin/python -m tools.jobs run-due --limit 50
NoNewPrivileges=true
PrivateTmp=true
MemoryMax=2G
CPUQuota=200%
TasksMax=256
```

`/etc/systemd/system/nest-jobs-run-due.timer`:

```ini
[Unit]
Description=Run Nest Jobs every minute

[Timer]
OnCalendar=*-*-* *:*:00
Persistent=true
AccuracySec=1s
Unit=nest-jobs-run-due.service

[Install]
WantedBy=timers.target
```

## cron 예시

```cron
* * * * * cd /srv/nest && /usr/bin/flock -n /run/nest-jobs.lock .venv/bin/python -m tools.jobs run-due --limit 50 >> /var/log/nest-jobs.log 2>&1
```

DB deduplication과 lease fencing이 최종 안전장치지만, `flock`으로 불필요한 중복
브라우저 프로세스 시작도 줄인다.

## Windows 작업 스케줄러 예시

프로그램:

```text
C:\nest\.venv\Scripts\python.exe
```

인수:

```text
-m tools.jobs run-due --limit 50
```

시작 위치는 `C:\nest`, 트리거는 1분 간격으로 설정한다. 작업이 이미 실행 중이면
새 인스턴스를 시작하지 않도록 구성하고, 서비스 계정의 환경변수/secret store로
설정을 주입한다.

## Production release gate

다음을 모두 증거로 남기기 전에는 `JOBS_WEB_COLLECTION_ENABLED=true`로 운영
릴리즈하지 않는다.

- Worker 호스트 또는 컨테이너의 outbound 정책이 loopback, RFC1918, link-local,
  cloud metadata endpoint를 네트워크 계층에서도 차단한다.
- DNS rebinding 상황에서도 브라우저가 내부 주소로 연결할 수 없도록 egress
  firewall 또는 강제 forward proxy가 적용된다.
- 허용 대상은 필요한 공용 HTTP(S)로 제한하고 방화벽/proxy 거부 로그를 보존한다.
- `JOBS_BROWSER_CONCURRENCY`, `MemoryMax`, `CPUQuota`, `TasksMax` 조합을 실제
  대상 페이지로 부하 시험한다.
- Transfer limit, timeout, popup, download, browser crash, saturation과 반복 context
  cleanup 테스트가 배포 Chromium 버전에서 통과한다.
- SIGTERM 시 현재 cycle을 마치고 Worker invocation이 `STOPPED`로 기록되며,
  강제 종료 시 lease 만료 후 retry가 단 한 번만 생성되는지 확인한다.
- Worker JSON 로그에 URL, 수집 내용, DB 경로, API key 또는 raw exception이 없는지
  확인한다.
- `tools.jobs health`의 heartbeat, queue depth와 서비스 manager의 재시작 횟수를
  모니터링한다.

애플리케이션 URL 정책은 방어 계층 중 하나이며 host egress 제어를 대체하지 않는다.
