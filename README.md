# yes-quiet

<img src="icon.png" width="96" align="right">

메신저 알림을 스위치 하나로 한꺼번에 끄고 켜는 Windows 앱. 앱을 종료하지 않고 알림만 끈다.

## 설치

1. [Releases](../../releases/latest)에서 `yes-quiet.exe`를 받는다.
2. 원하는 폴더에 두고 실행한다. 설치 과정은 없다.

서명되지 않은 exe라 처음 실행할 때 Windows SmartScreen 경고가 뜰 수 있다. "추가 정보" → "실행"을 누르면 된다.

## 사용법

실행하면 화면 오른쪽 아래에 고양이 얼굴만 뜬다(항상 위). 끌어서 옮기면 다음 실행 때도 그 자리에 뜬다.

1. 작업 표시줄 알림 영역의 고양이를 우클릭 → **대상 앱**에서 알림을 끌 메신저를 체크한다.
   지원 메신저: 카카오톡, Telegram, WhatsApp, Microsoft Teams, LINE, Discord, Slack
2. 화면의 고양이를 누르면 **알림 OFF**(회색 고양이), 다시 누르면 **알림 ON**(컬러 고양이).
3. 끄려면 알림 영역 고양이를 우클릭 → **종료**.

OFF 동안 하는 일:

- 대상 앱이 새로 띄우는 알림 팝업을 바로 숨긴다. 채팅창처럼 직접 연 창은 그대로 둔다.
  카카오톡은 오른쪽 아래 미리보기 창만 골라 숨기고, 메뉴 등 다른 창은 건드리지 않는다. 다른 메신저는 항상 위·도구 창을 팝업으로 본다.
- 대상 앱 소리를 음소거한다.
- Windows 알림 센터를 쓰는 앱은 Windows 알림 설정을 끈다.

OFF 상태에서 앱을 닫으면 팝업 차단도 멈춘다. 다시 실행하면 OFF 상태를 이어서 차단한다.

설정과 기록은 `%APPDATA%\yes-quiet\`에 저장된다.

## 소스에서 실행·빌드

```
pip install -r requirements.txt
python yes-quiet.pyw      # 실행
python test_yes_quiet.py  # 테스트
build.bat                 # dist\yes-quiet.exe 생성
```

아이콘은 `python make_icon.py`로 다시 그린다.
