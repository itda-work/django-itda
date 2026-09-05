"""orders 관리자 설정."""

from django.contrib import admin

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
    list_display = ('order', 'amount', 'status', 'requested_by', 'decided_by', 'created_at')
    list_filter = ('status',)
    search_fields = ('order__order_number', 'reason')
    readonly_fields = ('created_at',)
