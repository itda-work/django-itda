"""orders URL."""

from django.urls import path

from . import views

app_name = 'orders'

urlpatterns = [
    path('refunds/<int:pk>/approve/', views.approve_refund, name='approve-refund'),
    path('refunds/<int:pk>/reject/', views.reject_refund, name='reject-refund'),
]
