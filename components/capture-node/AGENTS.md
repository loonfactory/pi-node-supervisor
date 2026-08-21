# Capture Node Agent Guide

이 파일은 이 저장소에서 작업하는 개발 에이전트가 따라야 할 기본 지침이다.
사용자의 최신 요청과 시스템 지침이 이 문서보다 우선하며, 기존 사용자 변경사항은 명시적인 요청 없이 되돌리지 않는다.

## 1. 먼저 읽을 문서

작업 전에 요청과 관련된 문서를 확인하고, 구현과 문서가 충돌하면 임의로 한쪽을 덮어쓰지 말고 차이를 명확히 기록한다.

- 추가 기능의 현재 기준: `documents/CaptureNode_추가기능명세_v0.1.0.md`
- 주요 구현 결정과 이유: `documents/developer/implementation-decisions.md`
- 공통 설계 및 코딩 원칙: `documents/developer/common.md`
- 최초 범위와 요구사항: `documents/` 아래 기획서, 요구사항 명세서, 설계서, 테스트 케이스 문서

최초 범위 문서는 기준선 문서다. 이미 결정된 내용을 소리 없이 수정하지 말고, 새 동작이나 변경된 결정은 추가 기능 명세와 구현 결정 문서에 반영한다.

## 2. 기본 검증

```bash
.venv/bin/pytest -q
npm --prefix src/capture_node/web/frontend run build
```
