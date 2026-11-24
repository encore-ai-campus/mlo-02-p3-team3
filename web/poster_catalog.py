"""겨울 포스터의 공연 정보. 가격·좌석은 기존 데모 공연 기준의 임시 설정."""

WINTER_SHOWS = {
    'blue-hour-jazz': dict(
        id='blue-hour-jazz', title='겨울밤의 재즈', english='BLUE HOUR JAZZ',
        genre='콘서트', venue='달빛 재즈홀', date='2026.11.28', time='19:30',
        poster='01-blue-hour-jazz.png',
        tagline='색소폰 · 피아노 · 더블베이스',
        description='겨울밤의 재즈. 색소폰, 피아노, 더블베이스가 함께하는 공연입니다.'),
    'first-snow-classics': dict(
        id='first-snow-classics', title='첫눈의 선율', english='FIRST SNOW CLASSICS',
        genre='클래식', venue='서리 아트홀', date='2026.11.29', time='17:00',
        poster='02-first-snow-classic.png',
        tagline='바이올린 · 첼로 · 피아노',
        description='첫눈의 선율. 바이올린, 첼로, 피아노로 만나는 클래식 공연입니다.'),
    'november-last-night': dict(
        id='november-last-night', title='11월의 마지막 밤', english='NOVEMBER LAST NIGHT',
        genre='콘서트', venue='온기 라이브클럽', date='2026.11.30', time='20:00',
        poster='03-november-indie.png',
        tagline='INDIE LIVE',
        description='11월의 마지막 밤, 온기 라이브클럽에서 열리는 인디 라이브 공연입니다.'),
    'winter-waltz': dict(
        id='winter-waltz', title='겨울의 왈츠', english='WINTER WALTZ',
        genre='무용', venue='별빛 씨어터', date='2026.12.05', time='18:00',
        poster='04-winter-ballet.png',
        tagline='A WINTER BALLET',
        description='겨울의 왈츠. 별빛 씨어터에서 만나는 겨울 발레 공연입니다.'),
    'warm-soul': dict(
        id='warm-soul', title='마음을 녹이는 밤', english='WARM SOUL',
        genre='콘서트', venue='노을 뮤직홀', date='2026.12.06', time='19:00',
        poster='05-warm-soul.png',
        tagline='R&B · SOUL · LIVE',
        description='마음을 녹이는 밤. 노을 뮤직홀에서 열리는 R&B·소울 라이브 공연입니다.'),
}

for show in WINTER_SHOWS.values():
    if show['id'] in ('november-last-night', 'warm-soul'):
        show.update(price=35000, kind='general', kind_label='자유석')
    else:
        show.update(price=55000, kind='reserved', kind_label='지정석')
