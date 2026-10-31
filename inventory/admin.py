from django.contrib import admin
from .models import Item, Stock, Allocation, MovementRequest, AuditEvent, ImportBatch, Receipt, PickingOrder, ErpSnapshot, ReconciliationRow, LegacyArchiveBatch, LegacyArchiveRecord, Substitution, SubstitutionSettlement
@admin.register(Item)
class ItemAdmin(admin.ModelAdmin):
    list_display = ['code','name']
    search_fields = ['code','name']
    def save_model(self,request,obj,form,change):
        from .locking import warehouse_lock
        with warehouse_lock():super().save_model(request,obj,form,change)
    def get_readonly_fields(self,request,obj=None):
        return ['code'] if obj is not None and obj.stock_set.exists() else []
# Transactional records are read-only here; mutations must use audited services.
class ReadOnlyAdmin(admin.ModelAdmin):
    def has_add_permission(self, request): return False
    def has_change_permission(self, request, obj=None): return False
    def has_delete_permission(self, request, obj=None): return False
for model in [Stock, Allocation, MovementRequest, AuditEvent, ImportBatch, Receipt, PickingOrder, ErpSnapshot, ReconciliationRow, LegacyArchiveBatch, LegacyArchiveRecord, Substitution, SubstitutionSettlement]:
    admin.site.register(model, ReadOnlyAdmin)
