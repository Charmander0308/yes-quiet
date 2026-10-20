# 변경 기록

버전은 [유의적 버전](https://semver.org/lang/ko/)을 따른다. 버전 번호는 `yes-quiet.pyw`의 `__version__`과 git 태그(`v1.0.0`)를 함께 올린다.

## 1.0.0 - 2026-10-20

- 첫 공개 버전
- 알림 ON/OFF 스위치 하나와 대상 앱 목록(실행 중인 앱에서 추가·삭제)
- OFF 동안: 대상 앱의 새 알림 팝업 숨김, 소리 음소거, Windows 알림 설정 끄기
- ON으로 돌리면 소리·Windows 알림 설정을 원래대로 복구
- 설정은 `%APPDATA%\yes-quiet\config.json`에 저장
