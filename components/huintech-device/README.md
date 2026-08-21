# huintech-device

장치 드라이버를 언어별 최상위 디렉터리로 나누어 관리한다.

## Python

Python 패키지와 테스트는 `python/` 아래에 있다.

```sh
cd python
python -m unittest discover -s tests
```

## C++

C++ 소스는 `cpp/` 아래에 둔다. MCU 독립 SP485EN 코어의 빌드, 테스트 및
보드 통합 경계는 `cpp/README.md`에 설명되어 있다.
