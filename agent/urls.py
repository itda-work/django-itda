"""agent URL — 콘솔과 로그인.

AI 직원은 is_staff 가 아니라서 admin 로그인 화면을 쓸 수 없다. 콘솔용 로그인을 따로 둔다.
"""

from django.contrib.auth import views as auth_views
from django.urls import path

from . import views

app_name = 'agent'

urlpatterns = [
    path('', views.console, name='console'),
    path('act/<slug:bit>/', views.act, name='act'),
    path(
        'login/',
        auth_views.LoginView.as_view(template_name='agent/login.html'),
        name='login',
    ),
    path('logout/', auth_views.LogoutView.as_view(next_page='agent:login'), name='logout'),
]
