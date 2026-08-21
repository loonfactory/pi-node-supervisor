# CaptureNode 격리망 시각 동기화 운영 가이드

## 1. 목적과 범위

이 문서는 인터넷 gateway가 없는 전용 유선망에서 Windows 서버를 기준 시계로
사용하고 Raspberry Pi CaptureNode의 시스템 시계와 RTC를 유지하는 방법을
정의한다.

이 구성은 CaptureNode 외부의 host 운영 인프라다. Core, Web API, 촬영
scheduler 계약을 변경하지 않으며, 기준선 문서의 신뢰된 로컬 네트워크 전제와
촬영 시각 정확성을 지원한다. RTC와 NTP를 CaptureNode application 기능으로
구현하지 않는다.

현재 운영 주소는 다음과 같다.

| 역할 | 주소 | 비고 |
| --- | --- | --- |
| Windows NTP 서버 | `192.168.12.7/24` | 격리망의 권위 시계 |
| Raspberry Pi CaptureNode | `192.168.12.8/24` | NTP client, UTC RTC |

두 장비는 같은 `192.168.12.0/24` link에 있으므로 gateway와 DNS가 필요하지
않다. Wi-Fi와 외부 NTP는 이 구성에 포함하지 않는다.

## 2. Windows NTP 서버

완전 격리된 Windows 서버는 상위 NTP가 없는 root이므로 로컬 시계를 권위
시계로 제공한다. 관리자 PowerShell에서 다음을 실행한다.

```powershell
$w32Time = 'HKLM:\SYSTEM\CurrentControlSet\Services\W32Time'

Set-ItemProperty "$w32Time\Parameters" Type 'NoSync'
Set-ItemProperty "$w32Time\Config" AnnounceFlags 5
Set-ItemProperty "$w32Time\TimeProviders\NtpServer" Enabled 1

Set-Service W32Time -StartupType Automatic
Restart-Service W32Time

w32tm /config /reliable:yes /update
Restart-Service W32Time
```

`Type=NoSync`는 상위 시계를 찾지 않는 격리망 root임을 나타낸다.
`AnnounceFlags=5`는 서버를 항상 신뢰 가능한 시간 서버로 알리고,
`NtpServer.Enabled=1`은 NTP server provider를 활성화한다.

UDP 123은 CaptureNode에서 들어오는 요청만 허용한다.

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
  -LocalAddress 192.168.12.7 `
  -LocalPort 123 `
  -RemoteAddress 192.168.12.8
```

서버 설정을 확인한다.

```powershell
Get-Service W32Time
Get-NetUDPEndpoint -LocalPort 123
w32tm /query /configuration
w32tm /query /status
```

Windows 서버의 시각 자체가 틀리면 모든 CaptureNode가 같은 잘못된 시각으로
동기화된다. 서버 시각을 변경한 뒤에는 W32Time을 다시 시작하고 client에서
재검증한다.

## 3. Raspberry Pi 네트워크

NetworkManager connection은 DHCP, gateway와 DNS 없이 고정 주소를 사용한다.

```bash
sudo nmcli connection modify netplan-eth0 \
  ipv4.method manual \
  ipv4.addresses 192.168.12.8/24 \
  ipv4.gateway "" \
  ipv4.dns "" \
  ipv4.ignore-auto-dns yes \
  ipv4.never-default yes

sudo nmcli connection up netplan-eth0
```

`192.168.12.8/24` 주소가 활성화되면 kernel이
`192.168.12.0/24 dev eth0` connected route를 자동으로 만든다. 별도 default
route는 만들지 않는다.

```bash
ip -4 address show dev eth0
ip -4 route
ip -4 route get 192.168.12.7
```

정상적인 route 조회에는 `dev eth0 src 192.168.12.8`이 포함된다. Windows가
ICMP echo를 차단하면 ping이 실패할 수 있으므로 SSH 또는 NTP 성공 여부와 함께
판단한다.

## 4. Raspberry Pi NTP client

`/etc/systemd/timesyncd.conf.d/capture-node.conf`을 다음과 같이 설정한다.

```ini
[Time]
NTP=192.168.12.7
FallbackNTP=
RootDistanceMaxSec=15
```

Windows가 로컬 CMOS 시계를 기준으로 사용할 때 기본적으로 약 10초의 root
dispersion을 광고한다. `systemd-timesyncd`의 기본 허용 한도 5초는 이를
거절하므로 15초까지 수용한다. Windows가 광고하는 값을 임의로 낮춰 정확도를
과장하지 않는다.

```bash
sudo timedatectl set-ntp true
sudo systemctl restart systemd-timesyncd
sleep 15
timedatectl status
timedatectl timesync-status
```

정상 기준은 다음과 같다.

```text
System clock synchronized: yes
NTP service: active
Server: 192.168.12.7
Leap: normal
Stratum: 1
Root distance: 15s 미만
Packet count: 1 이상
```

`Server` 표시만으로 동기화가 입증되지는 않는다. `Packet count: 0`은 유효한
응답을 아직 수락하지 않았다는 뜻이다.

## 5. RTC

Pi 5 내장 RTC는 UTC로 유지한다. 한국 현지 시각보다 9시간 느리게 표시되는 것이
정상이다.

```bash
timedatectl status
date --utc
cat /sys/class/rtc/rtc0/name
cat /sys/class/rtc/rtc0/hctosys
cat /sys/class/rtc/rtc0/date
cat /sys/class/rtc/rtc0/time
```

정상 장치는 `rpi-rtc`이고 boot clock으로 사용되는 장치는 `hctosys=1`이다.
RTC가 1970년으로 표시되면 먼저 NTP로 시스템 시각을 맞춘 후 RTC에 기록한다.

`hwclock`이 있으면 다음 명령을 사용한다.

```bash
sudo /usr/sbin/hwclock --systohc --utc
sudo /usr/sbin/hwclock --show --utc
```

Debian 13에서 `hwclock`은 `util-linux-extra` package에 포함된다. 도구가 없으면
`timedatectl set-time`이 시스템 시각과 RTC를 함께 갱신하는 경로를 사용할 수
있지만, 정확한 시스템 시각을 먼저 확보해야 한다.

```bash
current_time="$(date '+%Y-%m-%d %H:%M:%S')"
sudo timedatectl set-ntp false
sudo timedatectl set-time "$current_time"
sudo timedatectl set-ntp true
```

RTC 기록과 RTC battery의 유지 능력은 별개다. 완전 전원 차단 후에도 시각을
유지하려면 Pi 5 RTC battery가 연결되어야 한다. 검증할 때는 정상 종료 후 전원을
분리하고, NTP 서버에 연결하기 전에 RTC로 복원된 시각을 확인한다.

## 6. NTP 응답 직접 검증

추가 package 없이 Pi에서 NTP server 응답 header를 확인할 수 있다.

```bash
python3 - <<'PY'
import socket

server = ("192.168.12.7", 123)
request = bytes([0x23]) + bytes(47)

with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as client:
    client.settimeout(5)
    client.sendto(request, server)
    response, address = client.recvfrom(512)

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

정상 server 응답은 `bytes=48`, `leap=0`, `mode=4`, `stratum=1` 이상이다.

## 7. 장애 판별

| 증상 | 의미 | 조치 |
| --- | --- | --- |
| `Network is unreachable` | connected route가 없음 | 주소 prefix와 `ip route get` 확인 |
| ping 실패, SSH 성공 | Windows ICMP 차단 가능 | NTP와 SSH 결과로 link 판단 |
| `Timed out waiting for reply` | UDP 123 응답 없음 | W32Time provider와 firewall 확인 |
| `leap=3`, `stratum=0` | 서버가 자신을 비동기 상태로 광고 | 격리망 root에 `NoSync`, `AnnounceFlags=5` 적용 |
| `Server has too large root distance` | Windows 약 10초, Pi 기본 한도 5초 | Pi에 `RootDistanceMaxSec=15` 적용 |
| `Packet count: 0` | 유효한 NTP packet을 수락하지 않음 | server header와 timesyncd journal 확인 |
| 시스템 시각 정상, RTC가 1970년 | RTC 초기 기록 누락 | 정확한 시스템 시각을 RTC에 기록 |
| 전원 차단 후 RTC 초기화 | RTC backup power 없음 | RTC battery 연결 및 재검증 |

다음 로그는 server 선택과 실제 실패 이유를 구분하는 기준이다.

```bash
sudo journalctl -u systemd-timesyncd -b --no-pager -n 50
```

## 8. 운영 검증 순서

1. Windows 서버 시각과 W32Time service, UDP 123 listener를 확인한다.
2. Pi의 `eth0` 주소와 `192.168.12.0/24` connected route를 확인한다.
3. 직접 NTP 요청으로 `leap=0`, `stratum>=1`을 확인한다.
4. `timedatectl`에서 동기화, packet count와 root distance를 확인한다.
5. UTC 시스템 시각과 RTC 시각을 비교한다.
6. CaptureNode service와 camera 상태를 확인한다.

```bash
systemctl --user is-enabled capture-node-web@camera0.service
systemctl --user is-active capture-node-web@camera0.service
curl --fail http://127.0.0.1:8000/api/health
```

## 9. 참고 자료

- [Microsoft: Windows Time Service tools and settings](https://learn.microsoft.com/en-us/windows-server/networking/windows-time-service/windows-time-service-tools-and-settings)
- [Microsoft: How the Windows Time Service works](https://learn.microsoft.com/en-us/windows-server/networking/windows-time-service/how-the-windows-time-service-works)
- [Microsoft: Configure an authoritative time server](https://learn.microsoft.com/en-us/troubleshoot/windows-server/active-directory/configure-authoritative-time-server)
- [timedatectl manual](https://man7.org/linux/man-pages/man1/timedatectl.1.html)
- [Debian trixie util-linux-extra file list](https://packages.debian.org/trixie/arm64/util-linux-extra/filelist)
