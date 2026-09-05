"""URL 설정.

자리가 둘이다. `/admin/` 은 점주가 확정하는 자리, `/agent/` 는 AI 직원이 제안하는 자리다.
"""

from django.contrib import admin
from django.urls import include, path

urlpatterns = [
    path('admin/', admin.site.urls),
    path('agent/', include('agent.urls')),
    path('orders/', include('orders.urls')),
]
