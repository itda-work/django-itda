"""궤적 admin — **읽기 전용**이다.

장부는 고쳐 쓸 수 있으면 장부가 아니다. 추가·수정·삭제 권한을 모두 닫는다
(그래서 admin 에서 이 표를 지우려는 시도는 화면에서부터 막힌다).
"""

from django.contrib import admin

from .models import ToolCall


@admin.register(ToolCall)
class ToolCallAdmin(admin.ModelAdmin):
    list_display = ('started_at', 'tool', 'actor', 'via', 'kind', 'outcome', 'error')
    list_filter = ('tool', 'via', 'kind', 'outcome', 'error')
    search_fields = ('call_id', 'reason')
    date_hierarchy = 'started_at'

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False
