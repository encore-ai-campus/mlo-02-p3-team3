from pathlib import Path
import html, json
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, PageBreak, KeepTogether
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib import colors
from reportlab.lib.enums import TA_LEFT
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.lib.pagesizes import A4
from pypdf import PdfReader

ROOT = Path(r'C:\workspace\Ticket_Guard')
OUT = ROOT / 'output' / 'project-report-2026-11-24'
OUT.mkdir(parents=True, exist_ok=True)
pdfmetrics.registerFont(TTFont('Malgun', r'C:\Windows\Fonts\malgun.ttf'))
pdfmetrics.registerFont(TTFont('MalgunBold', r'C:\Windows\Fonts\malgunbd.ttf'))
pdfmetrics.registerFontFamily('Malgun', normal='Malgun', bold='MalgunBold', italic='Malgun', boldItalic='MalgunBold')

pages = []
def page(title, intro=''):
    items = [('title', title)]
    if intro: items.append(('p', intro))
    pages.append(items)
    return items
def h(items, title, text):
    items.extend([('h', title), ('p', text)])

p = page('티켓가드 프로젝트 개인 작업 정리', '작성 기준 2026년 11월 24일 KST  |  작성 대상 류지예  |  작업 영역 develop 및 admin_dashboard')
h(p, '담당 역할과 주요 결과', '티켓가드에서 사용자 예매 웹의 화면과 이용 흐름을 개선하고, QA 실행 기능과 분리된 관리자 대시보드를 구축했다. 이후 Spark가 처리한 서버 이벤트를 근거로 사전·사후 이상 관측을 저장하고 관리자 화면에서 확인하는 기능을 추가했다. 최근에는 관리자 콘솔의 홈 화면, 사이드바, 상단바와 로그아웃 동작을 정리했다.')
p.append(('table', (['영역', '주요 작업', '현재 범위'], [
    ['사용자 예매 웹', '메인·공연 탐색 UI, 게시판, 입장 제한, 오류 화면', 'develop에 구현'],
    ['관리자 대시보드', '전용 앱 분리, MongoDB 조회, 인증 연결, 콘솔 UI', 'admin_dashboard에 구현'],
    ['이상 관측', 'Spark 적재·평가, 사전·사후 화면, 상태 구분', '파이프라인과 관리자 코드 구현'],
    ['공연 디자인', '초겨울 공연 포스터 5종 생성', '로컬 이미지 및 ZIP 저장'],
])))
h(p, '작업을 설명하는 핵심 문장', '사용자가 공연을 찾고 예매를 시작하는 화면과 접근 제어를 개선했으며, 운영자가 이벤트와 이상 관측의 근거를 확인할 수 있도록 관리자 앱을 분리하고 데이터 조회 및 탐지 화면을 구현했다.')
h(p, '브랜치 기준', 'develop의 확인 기준은 6b97ded이며 마지막 커밋일은 11월 11일이다. admin_dashboard의 확인 기준은 b123e82이며 마지막 커밋일은 11월 24일이다. develop의 현재 커밋은 admin_dashboard의 이력에 포함된다. 두 브랜치에 공통으로 존재하는 변경은 중복 실적으로 계산하지 않는다.')
h(p, '협업 범위', 'Git 작성자 정보와 커밋 제목을 함께 대조했다. 사용자 흐름 공개 지표, 네트워크 지표 및 기반 파이프라인에는 팀원 작업이 포함돼 있다. 이 문서는 직접 변경한 작업과 해당 기반을 사용한 연동 작업을 구분한다. 코드 구현 상태와 실제 서비스 배포·운영 검증 상태도 별도로 다룬다.')

p = page('사용자 예매 웹 작업', '주요 브랜치 develop  |  주요 작업 기간 2026년 11월 3일부터 11월 11일까지')
h(p, '메인 화면과 공연 탐색 개선', '메인 페이지와 공연 탐색 화면의 구성, 스타일, 공통 헤더 및 카드 표현을 조정했다. 공연 데이터와 화면을 연결하고 검색·장르 선택을 포함한 기존 탐색 흐름의 표현을 정리했다. 회원가입 진입 화면도 추가했으며, 이는 실제 회원 등록·인증 완료와 구분되는 화면 골격이다. 근거 f829fa8, e914709, 5638c48.')
h(p, '사용자 UI 통일과 공연 이미지 적용', '예매 웹의 공통 색상·여백·컴포넌트 표현을 맞추고 메인과 세부 화면의 시각적 일관성을 높였다. 11월 분위기의 공연 이미지를 교체하고 공연 데이터의 포스터 참조를 연결했다. 이번에 새로 생성한 포스터 5종은 별도 로컬 산출물이며 아직 사이트의 공연 데이터에 연결하지 않았다. 근거 5715739, 6b97ded.')
h(p, '공지사항과 자유게시판 화면', '공지사항과 자유게시판의 라우트·목록 템플릿·전용 스타일을 추가했다. 카테고리, 검색 입력과 빈 목록 안내를 구성했다. 현재 실제 페이지는 posts 빈 목록과 total_count 0을 전달하며 게시물 저장, 작성, 수정·삭제 및 실제 검색 결과 조회는 후속 범위다. 근거 5715739 및 web/board_views.py.')
h(p, '예매 입장 제한', '사이트 전체에서 5초 구간마다 최대 40개의 예매 시도를 입장시키도록 구성했다. 앞선 대기자가 없고 한도가 남으면 보안문자로 바로 이동하며, 한도를 소진하면 등록 순서대로 대기한다. 새로고침과 동일 요청 재시도는 입장 한도를 추가 소비하지 않는다. 총 대기열 길이에 200명 제한을 두는 구조는 아니다. 근거 5715739 및 docs/booking-admission.md.')
h(p, '오류 처리와 연결 상태 안내', '티켓 형태의 404·500 화면과 전용 스타일, 오류 미리보기 및 정적 자원 연결을 추가했다. 요청 식별자에서 ERR 번호를 만들어 사용자 제보와 서버 관측 이벤트를 대조할 수 있게 했다. 브라우저 지원·연결 상태 안내와 공통 테마도 정리했다. 근거 6b97ded.')
h(p, '로그인 실패 제한과 회귀 검사', '서명 쿠키로 구분한 브라우저별 로그인 실패 횟수를 메모리에서 관리하고 기본 5회 실패 시 300초 잠금을 적용했다. 입장 제한, 게시판, 오류 화면, 로그인 제한별 검사 코드를 추가했다. 로그인 제한은 쿠키 초기화·서버 재시작으로 상태가 사라질 수 있는 개발용 구현이다. 근거 6b97ded, web/login_guard.py 및 config/settings.py.')

p = page('관리자 대시보드 작업', '주요 브랜치 admin_dashboard  |  초기 분리 11월 9일  |  최근 UI 정리 11월 24일')
h(p, '관리자 전용 앱과 실행 구조 분리', '관리자 기능을 루트 admin_dashboard 폴더와 ticket_guard_admin 패키지로 구성했다. FastAPI 앱, 설정, 조회 모듈, 템플릿, 정적 자원과 관리자 전용 테스트를 분리하고 Docker 및 게이트웨이 연결을 추가했다. 관리자 앱은 QA 패키지를 import하지 않는 경계를 둔다. 근거 8c530f8.')
h(p, '관리자 인증과 조회 연결', '기존 Django 관리자 세션을 게이트웨이가 검증한 뒤 관리자 앱에 검증된 사용자 헤더를 전달하는 구조를 연결했다. MongoDB URI와 인증·네트워크 설정을 조정하고 관리자 화면의 이벤트 조회를 구성했다. 앱은 데이터를 조회하며, 운영 권한·계정 생성과 데이터 쓰기는 별도 책임으로 남긴다. 근거 8c530f8, 7982714.')
h(p, '실제 데이터와 시안 분리', '현재 경로는 /admin/의 공개 지표, /admin/db의 기존 이벤트 조회, /admin/preview의 고정 시안으로 나뉜다. DB 오류나 결과 부재를 정상값 또는 시안으로 바꾸지 않고 상태로 표시한다. 공개 지표와 네트워크 조회의 선행 구현은 팀원 작업이 포함된 공통 기반이다. 본인의 직접 기여는 전용 앱 초기 분리, 조회 연결 조정, UI 개선과 이상 탐지 연동이다.')
h(p, '관리자 화면과 공통 콘솔 UI 정리', '초기 관리자 테마·헤더·로고·셸 스타일을 정리하고 11월 23일에는 관리자 화면의 정렬과 레이아웃을 조정했다. 11월 24일에는 QA 홈과 연결되는 콘솔 메인 구성, 사이드바, 상단 헤더와 계정 영역의 배치를 순차적으로 수정했다. 일부 변경은 QA 화면의 공용 연결부에도 적용됐다. 근거 7982714, 9c27d2b, 73a9977, 006324a, 1ce0a53, b123e82.')
h(p, '상단바와 로그아웃 동작', '콘솔에 로그아웃 버튼과 상단바 고정을 추가했다. 로그아웃은 기존 /api/action/ 경로와 Django CSRF 검증을 사용하고, 성공 시 탭의 예매·이벤트 상태를 정리한 뒤 로그인 화면으로 이동한다. 실패 시 버튼을 복구하고 재시도 안내를 제공한다. 근거 c1a4d31, b123e82 및 static/console-account.js.')
h(p, 'QA와의 작업 경계', '관리자 전용 조회와 검사 파일을 중심으로 확장했다. 전체 프로젝트에는 QA가 분리한 격리 건수 조회와 공용 MongoDB 계약이 존재하며 이를 관리자 화면의 summary 구조에 종속시키지 않는 규칙을 유지한다. 최근 콘솔 템플릿 수정은 QA 자동화 엔진 전체를 직접 개발했다는 의미와 구분한다.')

p = page('Spark 이상 관측과 탐지 화면', '주요 커밋 f2a4e5c 및 73a9977  |  작업일 2026년 11월 23일')
h(p, '서버 이벤트에서 이상 관측 적재', '기존 ingest의 Kafka 토픽, Consumer Group, 체크포인트와 clean·quarantine 적재 경로를 유지하면서 정상화한 서버 이벤트의 최소 관측을 anomaly_observations에 저장했다. 같은 배치의 드라이버에서 제한된 시간창을 재계산하고 이상 근거, clean 판정 필드, 화면 공개 문서와 상태를 순서대로 저장한다.')
p.append(('table', (['규칙', '관측 근거'], [
    ['구매 요청 중첩', '서버에서 관측한 구매 요청 처리 구간의 중첩'],
    ['후속 구매 재요청', '동일 예매 시도의 후속 구매 요청 시간창'],
    ['여러 페이지 요청', '동일 계정·공연·회차에서 서로 다른 페이지의 구매 요청'],
    ['빠른 예매 완료', '대기열 진입부터 구매 성공까지의 관측 시간'],
    ['짧은 시간의 반복 구매', '시간창 안의 서로 다른 구매 성공'],
    ['계정 구매 수량', '동일 계정·공연·회차의 최근 1시간 구매 수량'],
])))
h(p, '사전과 사후 관점 제공', '관리자 이상 탐지 화면에서 사전 관측과 구매 이후 사후 관점을 전환하도록 구성했다. 규칙 카드, 감지 대상, 관측 근거와 상세 타임라인을 연결했다. 실제 조회와 고정 시안을 분리하며 여러 페이지 요청을 공유 계정 또는 봇의 확정 판정으로 표시하지 않는다.')
h(p, '중복과 지연 도착 처리', '이벤트·구매 식별자를 사용해 중복 집계를 줄이고, 늦게 도착한 좌석 판매·재고 부족 근거가 있으면 신호를 retracted 상태로 정정한다. 근거 부족, 정책 미설정, 유예 중, 지원하지 않는 입력을 정상 0과 구분한다. 정책이 비어 있는 규칙은 판정하지 않는다.')
h(p, '게시 상태와 한계', '관리자는 조회 전후 health 상태와 규칙 버전·게시 시각을 확인하고 오래된 자료의 수치 공개를 보류한다. 기본 표시는 최근 1시간이며 관측 상한 25000개, 범위 상한 101개, 게시물 상한 8MiB를 둔다. 현재 상관 계산은 제한된 드라이버 재계산이며 대규모 분산 판정이나 자동 차단 기능을 제공하지 않는다.')
h(p, '검사 코드와 적용 상태', '규칙 평가, 구매 중복 제거, 지연 정정, 게시 멱등성과 오류 복구, 미수집 상태, 관리자 인증·실제/시안 분리에 대한 테스트를 추가했다. 격리 Kafka·Spark·MongoDB 통합 시험 스크립트도 마련했다. 운영 ingest 적용, 실제 권한 변경 및 최신 환경에서의 시험 성공은 코드 존재만으로 완료 처리하지 않는다.')

p = page('날짜별 주요 작업 이력', '공통 이력은 한 번만 기재했으며 병합 커밋과 되돌린 구현은 주요 완료 작업에서 제외했다.')
rows = [
    ['11월 3일', 'develop', 'f829fa8', '메인 페이지 UI 개편'],
    ['11월 4일', 'develop', 'e914709', '공연 탐색 UI 및 표시 데이터 개선'],
    ['11월 4일', 'develop', '5638c48', '회원가입 화면 골격 추가'],
    ['11월 9일', 'admin_dashboard', '8c530f8', '관리자 전용 앱 생성과 DB 연결'],
    ['11월 9일', 'admin_dashboard', '7982714', 'MongoDB 접속과 관리자 셸 개선'],
    ['11월 9일', 'admin_dashboard', '9c27d2b', 'QA 연결 화면 UI 조정'],
    ['11월 10일', 'develop', '5715739', 'UI 통일, 게시판, 예매 입장 제한'],
    ['11월 11일', 'develop', '6b97ded', '오류 화면, 로그인 제한, 공통 테마'],
    ['11월 23일', 'admin_dashboard', 'f2a4e5c', 'Spark 이상 관측과 사전·사후 화면'],
    ['11월 23일', 'admin_dashboard', '73a9977', '관리자 UI와 이상 관측 처리 보완'],
    ['11월 24일', 'admin_dashboard', '006324a', '콘솔 메인 화면 구성 조정'],
    ['11월 24일', 'admin_dashboard', '1ce0a53', '메인 사이드바와 헤더 정렬'],
    ['11월 24일', 'admin_dashboard', 'c1a4d31', '로그아웃 버튼과 상단바 고정'],
    ['11월 24일', 'admin_dashboard', 'b123e82', '계정·탐색 영역 세부 조정'],
]
p.append(('table', (['일자', '주요 작업 맥락', '커밋', '변경 요약'], rows)))
h(p, '되돌린 변경 처리', '11월 9일의 2ac2133과 ebee288은 각각 7bfcab6과 3dafb1d로 되돌려졌다. 따라서 해당 최초 MongoDB·QA 내부 관리자 연결을 유지 중인 완료 기능으로 계산하지 않는다. 이후 8c530f8에서 관리자 전용 앱을 새로 구성한 흐름으로 정리한다.')
h(p, '작성자와 담당자 구분', '98d4793, 6df9c63, ee3ba72는 Git 작성자 이메일이 사용자와 같지만 제목에 설호현 담당 작업으로 명시돼 있어 개인 직접 성과에서 제외했다. 공개 흐름 지표와 네트워크 기능도 홍석환 커밋을 구분했다. 위 브랜치 열은 작업의 주요 맥락이며 Git이 커밋의 최초 작성 브랜치를 영구 기록한다는 뜻은 아니다.')

p = page('현재 구현 범위와 후속 작업', '기능 구현, 과거 검사 기록과 현재 실행 상태를 구분해 인수인계할 항목이다.')
h(p, '구현된 범위', '사용자 웹의 UI 통일·게시판 화면·입장 제한·오류 처리와 로그인 실패 제한이 코드에 반영돼 있다. 관리자 앱 분리와 인증·MongoDB 조회 연결, 콘솔 레이아웃 및 로그아웃 동작도 반영돼 있다. 이상 관측은 저장·평가·공개와 관리자 사전·사후 화면이 구현돼 있다.')
h(p, '검증 기록의 해석', '웹에는 admission, board, error, login_guard 전용 회귀 검사가 존재한다. 관리자에는 앱 분리·조회·페이지·이상 탐지 검사가, 파이프라인에는 이상 규칙·저장 및 격리 통합 검사 코드가 있다. 저장소의 과거 검증 문서에는 당시 단위·QA·격리 통합 시험 결과가 기록돼 있으나 대상 커밋과 날짜가 달라 현재 통과 수치로 합산하지 않는다. 이번 정리는 Git 이력과 코드·문서 대조이며 앱을 새로 실행하거나 테스트를 재실행한 검증 보고서는 아니다.')
p.append(('table', (['우선순위', '후속 작업', '완료 판단 기준'], [
    ['높음', '최신 관리자·이상 관측 환경 통합 확인', '동일 DB·권한·정책 설정으로 실제 게시와 화면 조회 확인'],
    ['높음', '최근 커밋의 회귀 검사 실행', '관리자·파이프라인·지정 QA 검사 결과를 커밋과 함께 기록'],
    ['높음', '실제 수치 갱신 계약 정리', '수동/자동 갱신 범위와 1시간/12시간 표시의 정합성 확인'],
    ['중간', '게시판과 회원가입 실제 기능 연결', '저장·조회·작성 권한 및 인증 흐름 검증'],
    ['중간', '공유 상태 저장과 운영 예매 준비', '재시작·다중 프로세스에서 입장/재고/예매 정합성 검증'],
    ['중간', '이상 이력 보존 정책과 규모 시험', '보존 기간·상한 초과·장시간 부하·복구 기준 확정'],
])))
h(p, '현재 개발 구조의 제한', '예매·재고·대기열과 로그인 실패 제한은 단일 프로세스 메모리 기반이다. 서버 재시작 시 초기화되며 다중 서버 정합성을 보장하지 않는다. 이상 관측은 설정된 규칙과 수집된 근거에 한정되며 관측 없음은 정상 판정을 뜻하지 않는다. 운영 전체 경로의 지연 목표나 탐지 정확도를 달성했다고 단정할 근거는 별도 실험이 필요하다.')
h(p, '이번 추가 산출물', '11월 말부터 12월 초를 배경으로 재즈, 클래식, 인디, 발레, 소울 공연 포스터 5종과 생성 프롬프트, ZIP을 저장했다. 공연명·장소·일정은 가상이며 웹에 적용하려면 static 자산과 공연 데이터 연결 작업이 남아 있다.')

p = page('근거 자료와 확인 기준', '로컬 저장소에서 확인한 브랜치별 소스와 커밋을 근거로 작성했다. 실제 자격 증명과 환경 파일 값은 포함하지 않았다.')
h(p, '확인한 기준 커밋', 'develop 6b97ded2cee3ac53b780d80969cba340c0a4139c<br/>admin_dashboard b123e821ab5ba953d924e6e791d7253fb0c7265c<br/>로컬 admin_dashboard와 origin/admin_dashboard의 커밋은 동일하다. 이번 작업에서 원격 fetch는 수행하지 않았으며 원격의 추가 변경 유무를 실시간 확인한 결과는 아니다.')
for title, text in [
    ('사용자 예매 웹 근거', 'develop 기준 README.md, docs/booking-admission.md, web/services.py, web/board_views.py, web/error_views.py, web/login_guard.py, config/settings.py 및 web/test_admission.py, web/test_board_pages.py, web/test_error_pages.py, web/test_login_guard.py.'),
    ('관리자 구조와 인증 근거', 'admin_dashboard 기준 admin_dashboard/README.md, src/ticket_guard_admin/app.py, dependencies.py, mongo_reader.py, user_flow/pages.py, templates/header.html 및 static/console-account.js. src 이하 경로는 admin_dashboard/src를 기준으로 한다.'),
    ('이상 관측 근거', 'admin_dashboard 기준 pipeline/docs/anomaly.md, pipeline/anomaly/rules.py, pipeline/anomaly/store.py, pipeline/ingestion/mongo_writer.py, pipeline/tests/test_anomaly.py, pipeline/tests/anomaly_integration.py 및 admin_dashboard/src/ticket_guard_admin/anomaly/reader.py, tests/test_admin_anomaly.py.'),
    ('협업과 과거 검증 기록', 'admin_dashboard 기준 admin_dashboard/LIVE_CONNECTION.md, PREVIEW_REVIEW.md, pipeline/docs/connection_work.md, pipeline/docs/validation.md 및 Git 작성자·변경 파일 이력. 오래된 README의 미연결 설명은 11월 23일 이상 탐지 코드의 현재 구현 상태와 구분했다.'),
    ('주요 개인 커밋', 'f829fa8, e914709, 5638c48, 8c530f8, 7982714, 9c27d2b, 5715739, 6b97ded, f2a4e5c, 73a9977, 006324a, 1ce0a53, c1a4d31, b123e82.'),
    ('팀원 기반으로 구분한 커밋', 'c41c234 사용자 흐름 공개 지표 연결, e435513 네트워크 지표 추가, b0703a8 사용자 흐름·DB 미연결 보완은 홍석환 작업이다. 98d4793, 6df9c63, ee3ba72는 제목에 설호현 담당 작업으로 표시돼 있다.'),
]: h(p, title, text)
p.append(('p', '저장소 https://github.com/seoloo/Ticket_Guard/'))

styles = getSampleStyleSheet()
styles.add(ParagraphStyle(name='KRBody', fontName='Malgun', fontSize=9.5, leading=15.8, textColor=colors.HexColor('#222222'), wordWrap='CJK', spaceAfter=10))
styles.add(ParagraphStyle(name='KRTitle', fontName='MalgunBold', fontSize=22, leading=30, textColor=colors.black, wordWrap='CJK', spaceAfter=15))
styles.add(ParagraphStyle(name='KRHead', fontName='MalgunBold', fontSize=11.3, leading=17, textColor=colors.black, wordWrap='CJK', spaceBefore=10, spaceAfter=5, keepWithNext=True))
styles.add(ParagraphStyle(name='KRCell', fontName='Malgun', fontSize=8.3, leading=13, wordWrap='CJK'))
styles.add(ParagraphStyle(name='KRCellHead', fontName='MalgunBold', fontSize=8.5, leading=13, wordWrap='CJK', textColor=colors.white))
story=[]
html_pages=[]
for i, items in enumerate(pages):
    if i: story.append(PageBreak())
    parts=[]
    for kind, value in items:
        if kind in ('title','h','p'):
            style={'title':'KRTitle','h':'KRHead','p':'KRBody'}[kind]
            story.append(Paragraph(value, styles[style]))
            tag={'title':'h1','h':'h2','p':'p'}[kind]
            parts.append(f'<{tag}>{value}</{tag}>')
        else:
            headers, rows=value
            data=[[Paragraph(html.escape(c), styles['KRCellHead']) for c in headers]]+[[Paragraph(html.escape(c), styles['KRCell']) for c in row] for row in rows]
            if len(headers)==4: widths=[64,91,58,274]
            elif headers[0]=='규칙': widths=[125,362]
            elif headers[0]=='우선순위': widths=[58,168,261]
            else: widths=[87,220,180]
            t=Table(data, colWidths=widths, repeatRows=1, hAlign='LEFT')
            t.setStyle(TableStyle([('BACKGROUND',(0,0),(-1,0),colors.HexColor('#26394B')),('ROWBACKGROUNDS',(0,1),(-1,-1),[colors.white, colors.HexColor('#F2F5F7')]),('GRID',(0,0),(-1,-1),0.5,colors.HexColor('#D9D9D9')),('VALIGN',(0,0),(-1,-1),'MIDDLE'),('LEFTPADDING',(0,0),(-1,-1),8),('RIGHTPADDING',(0,0),(-1,-1),8),('TOPPADDING',(0,0),(-1,-1),8),('BOTTOMPADDING',(0,0),(-1,-1),8)]))
            story.extend([Spacer(1,5),t,Spacer(1,10)])
            parts.append('<table><thead><tr>'+''.join('<th>'+html.escape(c)+'</th>' for c in headers)+'</tr></thead><tbody>'+''.join('<tr>'+''.join('<td>'+html.escape(c)+'</td>' for c in row)+'</tr>' for row in rows)+'</tbody></table>')
    html_pages.append('<section>'+''.join(parts)+'</section>')

pdf_path=OUT/'TicketGuard_개인작업정리_20261124.pdf'
def footer(canvas, doc):
    canvas.setFont('Malgun',8)
    canvas.setFillColor(colors.HexColor('#666666'))
    canvas.drawString(54,30,'TicketGuard  |  류지예  |  2026.11.24')
    canvas.drawRightString(A4[0]-54,30,str(doc.page))
doc=SimpleDocTemplate(str(pdf_path), pagesize=A4, rightMargin=54,leftMargin=54,topMargin=46,bottomMargin=50, title='티켓가드 프로젝트 개인 작업 정리',author='류지예')
doc.build(story, onFirstPage=footer,onLaterPages=footer)
css='''body{margin:0;background:#edf0f3;color:#222;font:15px/1.8 "Malgun Gothic",sans-serif}main{max-width:900px;margin:32px auto}section{box-sizing:border-box;padding:50px 60px;margin-bottom:24px;background:white;box-shadow:0 2px 12px #0001}h1{font-size:28px;color:#000;line-height:1.4;margin:0 0 22px}h2{font-size:18px;color:#000;margin:24px 0 8px}p{margin:0 0 16px;overflow-wrap:anywhere}table{width:100%;border-collapse:collapse;margin:20px 0}th,td{border:1px solid #d9d9d9;padding:10px;text-align:left;vertical-align:middle;font-size:13px}th{background:#26394b;color:white}tr:nth-child(even){background:#f2f5f7}@media print{body{background:white}main{margin:0;max-width:none}section{box-shadow:none;padding:0;break-after:page}section:last-child{break-after:auto}h2{break-after:avoid}tr{break-inside:avoid}}@media(max-width:650px){section{padding:24px}main{margin:10px}table{font-size:12px}}'''
(OUT/'TicketGuard_개인작업정리_20261124.html').write_text('<!doctype html><html lang="ko"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>티켓가드 프로젝트 개인 작업 정리</title><style>'+css+'</style><main>'+''.join(html_pages)+'</main></html>',encoding='utf-8')
(OUT/'report-content.json').write_text(json.dumps(pages,ensure_ascii=False,indent=2),encoding='utf-8')
r=PdfReader(str(pdf_path))
print('PDF pages:',len(r.pages))
print('Page text lengths:',[len(p.extract_text()) for p in r.pages])
print(pdf_path)
