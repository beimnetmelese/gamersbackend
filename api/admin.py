from django.contrib import admin
from .models import (
    UserProfile, SellerProfile, Product, Game, GameParticipant,
    GameResult, Favorite, Wallet, WalletTransaction, PaymentSubmission,
    WithdrawalRequest, ProductDelivery, Notification, AuditLog
)

@admin.register(UserProfile)
class UserProfileAdmin(admin.ModelAdmin):
    list_display = ['user', 'role', 'phone_number', 'account_status', 'created_at']
    list_filter = ['role', 'account_status']

@admin.register(SellerProfile)
class SellerProfileAdmin(admin.ModelAdmin):
    list_display = ['business_name', 'user', 'status', 'created_at']
    list_filter = ['status']

@admin.register(Product)
class ProductAdmin(admin.ModelAdmin):
    list_display = ['title', 'seller', 'category', 'estimated_value', 'approval_status']
    list_filter = ['approval_status', 'category']

@admin.register(Game)
class GameAdmin(admin.ModelAdmin):
    list_display = ['title', 'game_type', 'entry_fee', 'status', 'created_at']
    list_filter = ['game_type', 'status']

@admin.register(GameParticipant)
class GameParticipantAdmin(admin.ModelAdmin):
    list_display = ['game', 'user', 'joined_at']

@admin.register(GameResult)
class GameResultAdmin(admin.ModelAdmin):
    list_display = ['game', 'winner', 'calculated_at']

@admin.register(Favorite)
class FavoriteAdmin(admin.ModelAdmin):
    list_display = ['user', 'game', 'created_at']

@admin.register(Wallet)
class WalletAdmin(admin.ModelAdmin):
    list_display = ['user', 'balance', 'reserved_balance', 'updated_at']

@admin.register(WalletTransaction)
class WalletTransactionAdmin(admin.ModelAdmin):
    list_display = ['wallet', 'transaction_type', 'direction', 'amount', 'status', 'created_at']
    list_filter = ['transaction_type', 'direction', 'status']

@admin.register(PaymentSubmission)
class PaymentSubmissionAdmin(admin.ModelAdmin):
    list_display = ['user', 'payment_method', 'transaction_id', 'amount', 'status', 'submitted_at']
    list_filter = ['status', 'payment_method']

@admin.register(WithdrawalRequest)
class WithdrawalRequestAdmin(admin.ModelAdmin):
    list_display = ['user', 'withdrawal_method', 'account_number', 'amount', 'status', 'submitted_at']
    list_filter = ['status', 'withdrawal_method']

@admin.register(ProductDelivery)
class ProductDeliveryAdmin(admin.ModelAdmin):
    list_display = ['game_result', 'winner', 'seller', 'status', 'updated_at']
    list_filter = ['status']

@admin.register(Notification)
class NotificationAdmin(admin.ModelAdmin):
    list_display = ['user', 'title', 'event_type', 'is_read', 'created_at']

@admin.register(AuditLog)
class AuditLogAdmin(admin.ModelAdmin):
    list_display = ['actor', 'action', 'target_model', 'timestamp']
