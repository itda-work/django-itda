"""orders 관리자 설정."""

from django.contrib import admin, messages
from django.http import HttpResponseRedirect

from .models import Order, OrderItem, Refund


class OrderItemInline(admin.TabularInline):
    model = OrderItem
    extra = 0
    readonly_fields = ('product_name', 'unit_price', 'quantity', 'total_price_readonly')

    @admin.display(description='소계')
    def total_price_readonly(self, item):
        return f'{item.total_price:,}원'


@admin.register(Order)
class OrderAdmin(admin.ModelAdmin):
    list_display = ('order_number', 'user', 'status', 'total_amount', 'created_at')
    list_filter = ('status',)
    search_fields = ('order_number', 'user__username', 'recipient_name')
    inlines = (OrderItemInline,)
    readonly_fields = ('order_number', 'created_at', 'updated_at')


@admin.register(Refund)
class RefundAdmin(admin.ModelAdmin):
    """점주의 승인 큐.

    상태·결정자는 손으로 못 고친다. 오직 액션(승인/거부)을 통해서만 바뀐다.
    누가 이 액션을 쓸 수 있는지는 여기서 정하지 않는다 — `orders.change_refund`
    권한을 가졌는지를 Django 가 알아서 본다.
    """

    list_display = (
        'order',
        'order_status',
        'amount',
        'status',
        'requested_by',
        'decided_by',
        'created_at',
    )
    list_filter = ('status',)
    search_fields = ('order__order_number', 'reason')
    readonly_fields = ('status', 'requested_by', 'decided_by', 'decided_at', 'created_at')
    actions = ('approve_selected', 'reject_selected')

    @admin.display(description='주문 상태', ordering='order__status')
    def order_status(self, refund):
        return refund.order.get_status_display()

    def changelist_view(self, request, extra_context=None):
        """승인 큐는 '제안됨'부터 보여준다 — 점주가 볼 것은 대기 중인 제안이다."""
        if request.method == 'GET' and not request.GET:
            return HttpResponseRedirect(f'{request.path}?status={Refund.Status.PROPOSED}')
        return super().changelist_view(request, extra_context)

    @admin.action(description='선택한 환불을 승인', permissions=['change'])
    def approve_selected(self, request, queryset):
        done = 0
        for refund in queryset:
            refund.approve(request.user)
            done += 1
        self.message_user(request, f'{done}건을 승인했습니다.', messages.SUCCESS)

    @admin.action(description='선택한 환불을 거부', permissions=['change'])
    def reject_selected(self, request, queryset):
        done = 0
        for refund in queryset:
            refund.reject(request.user)
            done += 1
        self.message_user(request, f'{done}건을 거부했습니다.', messages.INFO)
