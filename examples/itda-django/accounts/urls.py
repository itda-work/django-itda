"""고객의 문 — 로그인·로그아웃.

6단계에서 세계에 사람이 둘이 된다. 지금까지의 로그인은 둘뿐이었다 —
점주는 `/admin/`, AI 직원은 `/agent/login/`. **고객은 자리가 없었다.**

결제 링크를 여는 사람이 누구인지 물으려면 먼저 그 사람이 로그인할 수 있어야
한다. 그래서 이 파일이 생긴다. `LOGIN_URL` 은 바꾸지 않는다 — 콘솔 뷰가 그것을
쓰고 있고, 결제 페이지만 자기 로그인 자리를 명시한다(`pay_page` 의 `login_url`).
"""

from django.contrib.auth import views as auth_views
from django.urls import path

app_name = 'accounts'

urlpatterns = [
    path(
        'login/',
        auth_views.LoginView.as_view(template_name='accounts/login.html'),
        name='login',
    ),
    path('logout/', auth_views.LogoutView.as_view(next_page='accounts:login'), name='logout'),
]
