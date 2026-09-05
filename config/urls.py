"""URL 설정.

0단계의 세계는 admin 하나뿐이다. 점주는 admin에서 세계를 들여다본다.
"""

from django.contrib import admin
from django.urls import path

urlpatterns = [
    path('admin/', admin.site.urls),
]
