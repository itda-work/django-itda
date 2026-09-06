"""URL 설정.

자리가 둘이다. `/admin/` 은 점주가 확정하는 자리, `/agent/` 는 AI 직원이 제안하는 자리다.
`/api/` 는 브라우저 밖에서 같은 자리에 앉는 문이다 — 실접속(live) 트랙의 MCP 서버가
`ai-staff` 토큰으로 두드린다. 화면만 다르고 판정은 `/agent/` 와 같다.
"""

from django.contrib import admin
from django.urls import include, path

urlpatterns = [
    path('admin/', admin.site.urls),
    path('agent/', include('agent.urls')),
    path('orders/', include('orders.urls')),
    path('api/', include('orders.api')),
]
