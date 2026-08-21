# 격리망 CaptureNode 시각 동기화 운영 가이드

| 항목 | 값 |
|---|---|
| 문서 ID | OPS-CAPTURENODE-TIME-001 |
| 버전 | v0.1.0 |
| 상태 | Draft |
| 작성일 | 2026-08-12 |
| 보안 등급 | 내부 |
| 배포 대상 | 운영 및 개발 관계자 |

## 1. 목적과 범위

이 문서는 인터넷 게이트웨이가 없는 전용 유선망에서 Windows 서버를 기준 시계로
사용하고 Raspberry Pi CaptureNode의 시스템 시계와 RTC를 유지하는 방법을
정의한다.

이 구성은 CaptureNode 애플리케이션 외부의 호스트 운영 인프라다. Core, Web API,
촬영 스케줄러 계약을 변경하지 않으며, 기준선 문서의 신뢰된 로컬 네트워크 전제와
촬영 시각 정확성을 지원한다. RTC와 NTP를 CaptureNode 애플리케이션 기능으로
구현하지 않는다.

이 문서의 주소와 장치 이름은 모두 설명을 위한 예시이며 실제 운영값이 아니다.
IP 주소에는 문서 예시용으로 예약된 TEST-NET-1 대역을 사용한다. 명령을 실행하기
전에 아래 값을 실제 환경에 맞게 바꿔야 한다.

| 항목 | 문서의 예시 값 | 적용 시 변경 사항 |
|---|---|---|
| 전용망 | `192.0.2.0/24` | 운영 환경의 전용망 대역 |
| Windows NTP 서버 | `192.0.2.7` | 격리망에서 기준 시계로 사용할 서버 주소 |
| Raspberry Pi CaptureNode | `192.0.2.8` | CaptureNode에 할당할 고정 주소 |
| 네트워크 인터페이스 | `eth0` | `ip link`에서 확인한 유선 인터페이스 이름 |
| NetworkManager 연결 | `capture-node-ethernet` | `nmcli connection show`에서 확인한 연결 이름 |

예시에서는 두 장비가 같은 링크에 있으므로 게이트웨이와 DNS가 필요하지 않다.
Wi-Fi와 외부 NTP는 이 구성에 포함하지 않는다. TEST-NET-1 주소를 그대로 운영에
사용하지 않는다.

> **정확도 주의:** 이 구성에서 Windows 서버는 외부 기준 시계와 동기화되지
> 않는다. 따라서 장비 사이의 시각은 일치시킬 수 있지만 UTC에 대한 절대 정확도는
> Windows 서버의 로컬 시계 정확도와 운영자의 교정에 의존한다.

## 2. Windows NTP 서버

완전히 격리된 Windows 서버는 상위 NTP가 없는 루트이므로 로컬 시계를 권위
시계로 제공한다. 관리자 PowerShell에서 다음을 실행한다.

```powershell
$w32Time = 'HKLM:\SYSTEM\CurrentControlSet\Services\W32Time'

Set-ItemProperty "$w32Time\Parameters" Type 'NoSync'
Set-ItemProperty "$w32Time\Config" AnnounceFlags 5
Set-ItemProperty "$w32Time\TimeProviders\NtpServer" Enabled 1

Set-Service W32Time -StartupType Automatic
Restart-Service W32Time
```

`Type=NoSync`는 상위 시계를 찾지 않는 격리망 루트임을 나타낸다.
`AnnounceFlags=5`는 서버를 항상 신뢰할 수 있는 시간 서버로 알리고,
`NtpServer.Enabled=1`은 NTP 서버 공급자를 활성화한다.

UDP 123은 CaptureNode에서 들어오는 요청만 허용한다. 같은 표시 이름의 기존 규칙을
교체하므로, 해당 규칙을 다른 용도로 사용하고 있지 않은지 먼저 확인한다.

```powershell
Get-NetFirewallRule -DisplayName 'CaptureNode NTP' `
  -ErrorAction SilentlyContinue |
  Remove-NetFirewallRule

New-NetFirewallRule `
  -DisplayName 'CaptureNode NTP' `
  -Enabled True `
  -Profile Any `
  -Direction Inbound `
  -Action Allow `
  -Protocol UDP `
  -LocalAddress 192.0.2.7 `
  -LocalPort 123 `
  -RemoteAddress 192.0.2.8
```

서버 설정을 확인한다.

```powershell
Get-Service W32Time
Get-NetUDPEndpoint -LocalPort 123
w32tm /query /configuration
w32tm /query /status
```

Windows 서버의 시각 자체가 틀리면 모든 CaptureNode가 같은 잘못된 시각으로
동기화된다. 서버 시각을 변경한 뒤에는 W32Time을 다시 시작하고 클라이언트에서
재검증한다.

## 3. Raspberry Pi 네트워크

NetworkManager 연결은 DHCP, 게이트웨이와 DNS 없이 고정 주소를 사용한다. 먼저
`nmcli connection show`로 실제 연결 이름을 확인한다. 아래 명령의
`capture-node-ethernet`은 예시 이름이다.

```bash
sudo nmcli connection modify capture-node-ethernet \
  ipv4.method manual \
  ipv4.addresses 192.0.2.8/24 \
  ipv4.gateway "" \
  ipv4.dns "" \
  ipv4.ignore-auto-dns yes \
  ipv4.never-default yes

sudo nmcli connection up capture-node-ethernet
```

`192.0.2.8/24` 주소가 활성화되면 커널이
`192.0.2.0/24 dev eth0` 연결 경로를 자동으로 만든다. 별도 기본 경로는 만들지
않는다.

```bash
ip -4 address show dev eth0
ip -4 route
ip -4 route get 192.0.2.7
```

정상적인 경로 조회에는 `dev eth0 src 192.0.2.8`이 포함된다. Windows가 ICMP
echo를 차단하면 ping이 실패할 수 있으므로 SSH 또는 NTP 성공 여부와 함께
판단한다.

## 4. Raspberry Pi NTP 클라이언트

`/etc/systemd/timesyncd.conf.d/capture-node.conf`을 만들고 다음과 같이 설정한다.
필요하면 먼저 `sudo install -d /etc/systemd/timesyncd.conf.d`로 디렉터리를 만든다.

```ini
[Time]
NTP=192.0.2.7
FallbackNTP=
RootDistanceMaxSec=15
```

Windows가 로컬 CMOS 시계를 기준으로 사용할 때 기본적으로 10초의 로컬 시계
분산값을 광고한다. `systemd-timesyncd`의 기본 허용 한도 5초는 이를 거절하므로
15초까지 수용한다. Windows가 광고하는 값을 임의로 낮춰 정확도를 과장하지 않는다.

```bash
sudo timedatectl set-ntp true
sudo systemctl restart systemd-timesyncd
sleep 15
timedatectl status
timedatectl timesync-status
```

정상 기준은 다음과 같다. 출력 항목과 표기 방식은 systemd 버전에 따라 다를 수
있다.

```text
System clock synchronized: yes
NTP service: active
Server: 192.0.2.7
Leap: normal
Stratum: 1
Root distance: 15s 미만
Packet count: 1 이상
```

`Server` 표시만으로 동기화가 입증되지는 않는다. `Packet count: 0`은 유효한
응답을 아직 수락하지 않았다는 뜻이다.

## 5. RTC

Pi 5 내장 RTC는 UTC로 유지한다. 시스템 시간대가 `Asia/Seoul`이면 RTC가 한국
현지 시각보다 9시간 느리게 표시되는 것이 정상이다.

```bash
timedatectl status
date --utc
cat /sys/class/rtc/rtc0/name
cat /sys/class/rtc/rtc0/hctosys
cat /sys/class/rtc/rtc0/date
cat /sys/class/rtc/rtc0/time
```

정상 장치는 `rpi-rtc`이고 부팅 시 시스템 시계를 초기화한 RTC는 `hctosys=1`로
표시된다. RTC가 1970년으로 표시되면 먼저 NTP로 시스템 시각을 맞춘 후 RTC에
기록한다.

`hwclock`이 있으면 다음 명령을 사용한다.

```bash
sudo /usr/sbin/hwclock --systohc --utc
sudo /usr/sbin/hwclock --show --utc
```

Debian 13에서 `hwclock`은 `util-linux-extra` 패키지에 포함된다. 도구가 없으면
`timedatectl set-time`이 시스템 시각과 RTC를 함께 갱신하는 경로를 사용할 수
있지만, 정확한 시스템 시각을 먼저 확보해야 한다.

```bash
current_time="$(date '+%Y-%m-%d %H:%M:%S')"
sudo timedatectl set-ntp false
sudo timedatectl set-time "$current_time"
sudo timedatectl set-ntp true
```

RTC 기록과 RTC 배터리의 유지 능력은 별개다. 완전 전원 차단 후에도 시각을
유지하려면 Pi 5 RTC 배터리가 연결되어야 한다. 검증할 때는 정상 종료 후 전원을
분리하고, NTP 서버에 연결하기 전에 RTC로 복원된 시각을 확인한다.

## 6. NTP 응답 직접 검증

추가 패키지 없이 Pi에서 NTP 서버 응답 헤더를 확인할 수 있다.

```bash
python3 - <<'PY'
import socket

server = ("192.0.2.7", 123)
request = bytes([0x23]) + bytes(47)

with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as client:
    client.settimeout(5)
    client.sendto(request, server)
    response, address = client.recvfrom(512)

if len(response) < 48:
    raise RuntimeError(f"short NTP response: {len(response)} bytes")

header = response[0]
print(
    f"from={address[0]}:{address[1]} "
    f"bytes={len(response)} "
    f"leap={header >> 6} "
    f"version={(header >> 3) & 7} "
    f"mode={header & 7} "
    f"stratum={response[1]}"
)
PY
```

정상 서버 응답은 `bytes=48` 이상, `leap=0`, `mode=4`, `stratum=1..15`다.

## 7. 장애 판별

| 증상 | 의미 | 조치 |
|---|---|---|
| `Network is unreachable` | 연결 경로가 없음 | 주소 prefix와 `ip route get` 확인 |
| ping 실패, SSH 성공 | Windows ICMP 차단 가능 | NTP와 SSH 결과로 링크 판단 |
| `Timed out waiting for reply` | UDP 123 응답 없음 | W32Time 공급자와 방화벽 확인 |
| `leap=3`, `stratum=0` | 서버가 자신을 비동기 상태로 광고 | 격리망 루트에 `NoSync`, `AnnounceFlags=5` 적용 |
| `Server has too large root distance` | Windows 약 10초, Pi 기본 한도 5초 | Pi에 `RootDistanceMaxSec=15` 적용 |
| `Packet count: 0` | 유효한 NTP 패킷을 수락하지 않음 | 서버 헤더와 timesyncd 저널 확인 |
| 시스템 시각 정상, RTC가 1970년 | RTC 초기 기록 누락 | 정확한 시스템 시각을 RTC에 기록 |
| 전원 차단 후 RTC 초기화 | RTC 백업 전원 없음 | RTC 배터리 연결 및 재검증 |

다음 로그는 서버 선택과 실제 실패 이유를 구분하는 기준이다.

```bash
sudo journalctl -u systemd-timesyncd -b --no-pager -n 50
```

## 8. 운영 검증 순서

1. Windows 서버 시각과 W32Time 서비스, UDP 123 리스너를 확인한다.
2. Pi의 `eth0` 주소와 `192.0.2.0/24` 연결 경로를 확인한다.
3. 직접 NTP 요청으로 `leap=0`, `stratum=1..15`를 확인한다.
4. `timedatectl`에서 동기화, 패킷 수와 루트 거리를 확인한다.
5. UTC 시스템 시각과 RTC 시각을 비교한다.
6. CaptureNode 서비스와 카메라 상태를 확인한다.

```bash
systemctl --user is-enabled capture-node-web@camera0.service
systemctl --user is-active capture-node-web@camera0.service
curl --fail http://127.0.0.1:8000/api/health
```

## 9. 참고 자료

- [Microsoft — Windows Time Service tools and settings](https://learn.microsoft.com/en-us/windows-server/networking/windows-time-service/windows-time-service-tools-and-settings)
- [Microsoft — How the Windows Time Service works](https://learn.microsoft.com/en-us/windows-server/networking/windows-time-service/how-the-windows-time-service-works)
- [Microsoft — Configure an authoritative time server](https://learn.microsoft.com/en-us/troubleshoot/windows-server/active-directory/configure-authoritative-time-server)
- [Debian trixie — timesyncd.conf(5)](https://manpages.debian.org/trixie/systemd-timesyncd/timesyncd.conf.5.en.html)
- [Debian trixie — timedatectl(1)](https://manpages.debian.org/trixie/systemd/timedatectl.1.en.html)
- [Debian trixie — util-linux-extra arm64 file list](https://packages.debian.org/trixie/arm64/util-linux-extra/filelist)
- [Raspberry Pi — Real Time Clock (RTC)](https://www.raspberrypi.com/documentation/computers/raspberry-pi.html#real-time-clock-rtc)
- [RFC 5737 — IPv4 Address Blocks Reserved for Documentation](https://www.rfc-editor.org/rfc/rfc5737.html)
