"""URL 설정.

자리가 넷이다. `/admin/` 은 점주가 확정하는 자리, `/agent/` 는 AI 직원이 제안하는
자리다. `/api/` 는 브라우저 밖에서 같은 자리에 앉는 문이다 — 실접속(live) 트랙의
MCP 서버가 `ai-staff` 토큰으로 두드린다. 화면만 다르고 판정은 `/agent/` 와 같다.

넷째가 6단계에서 생긴다 — `/pay/` 는 **고객**이 결제를 확정하는 자리이고,
`/accounts/` 는 그 고객이 로그인하는 자리다. 세계에 사람이 둘이 됐다.
"""

from django.contrib import admin
from django.urls import include, path

from orders import views as orders_views

# 403 화면은 두 자리로 갈린다 — 콘솔은 세계의 목소리, 그 외는 일반 403(`config/views.py`).
handler403 = 'config.views.permission_denied'

urlpatterns = [
    path('admin/', admin.site.urls),
    path('agent/', include('agent.urls')),
    path('orders/', include('orders.urls')),
    path('api/', include('orders.api')),
    path('accounts/', include('accounts.urls')),
    # 결제 페이지는 `/orders/` 아래가 아니라 `/pay/` 다 — 짧아서가 아니라,
    # `/orders/` 는 콘솔 버튼이 두드리는 확정 엔드포인트(AI 가 읽는 자리)이고
    # 여기는 **사람이 여는 페이지**라 403 화면부터 다르기 때문이다(`config/views.py`).
    path('pay/<int:pk>/<str:token>/', orders_views.pay_page, name='pay'),
]
