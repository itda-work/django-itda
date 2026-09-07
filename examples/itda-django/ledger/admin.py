"""장부 admin — **읽기 전용**이다(`django_itda.ToolCallAdmin` 과 같은 모양).

고칠 수 있는 장부는 장부가 아니다. 여기서 닫는 것은 화면 한 겹이고,
진짜 보장은 모델 층(`Event.save`/`delete`)과 DB 트리거에 있다.
"""

from django.contrib import admin

from .models import Event


@admin.register(Event)
class EventAdmin(admin.ModelAdmin):
    list_display = ('at', 'door', 'actor_label', 'transition', 'subject_label', 'kind')
    list_filter = ('door', 'transition', 'kind')
    search_fields = ('call_id', 'subject_label')
    date_hierarchy = 'at'

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False
