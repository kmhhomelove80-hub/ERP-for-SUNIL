from django.contrib import admin
from django.contrib.auth import views as auth_views
from django.urls import path
from inventory import views
from .auth_views import WarehouseLoginView
urlpatterns = [
    path('admin/',admin.site.urls),
    path('login/',WarehouseLoginView.as_view(),name='login'),
    path('logout/',auth_views.LogoutView.as_view(),name='logout'),
    path('',views.stock_list,name='stock-list'),
    path('picking/',views.picking_list,name='picking-list'),
    path('allocation-history/',views.allocation_history,name='allocation-history'),
    path('picking/new/',views.picking_create,name='picking-create'),
    path('picking/<uuid:pk>/substitute/',views.substitution_create,name='substitution-create'),
    path('substitutions/',views.substitution_list,name='substitution-list'),
    path('substitutions/<uuid:pk>/',views.substitution_detail,name='substitution-detail'),
    path('picking/<uuid:pk>/customer/',views.customer_picking_print,name='customer-picking-print'),
    path('picking/<uuid:pk>/',views.picking_detail,name='picking-detail'),
    path('allocations/',views.allocation_list,name='allocation-list'),
    path('movements/',views.movement_list,name='movement-list'),
    path('movements/<uuid:pk>/',views.movement_detail,name='movement-detail'),
    path('stock/export/',views.stock_export,name='stock-export'),
    path('stock/<int:pk>/allocations/',views.stock_allocations,name='stock-allocations'),
    path('stock/<int:pk>/move/',views.movement_create,name='movement-create'),
    path('archives/',views.archive_list,name='archive-list'),
    path('operations/',views.operations_status,name='operations-status'),
    path('history/',views.history_list,name='history-list'),
    path('erp/',views.erp_list,name='erp-list'),
    path('erp/upload/',views.erp_upload,name='erp-upload'),
    path('erp/template/',views.erp_template,name='erp-template'),
    path('erp/<uuid:pk>/export/',views.erp_export,name='erp-export'),
    path('erp/<uuid:pk>/',views.erp_detail,name='erp-detail'),
    path('erp/rows/<int:pk>/review/',views.erp_review,name='erp-review'),
    path('receipts/',views.receipt_list,name='receipt-list'),
    path('receipts/<uuid:pk>/cancel/',views.receipt_cancel,name='receipt-cancel'),
    path('receipts/bulk/',views.receipt_bulk,name='receipt-bulk'),
    path('receipts/new/',views.receipt_create,name='receipt-create'),
    path('stock/<int:pk>/arrival/',views.arrival_confirm,name='arrival-confirm'),
]
