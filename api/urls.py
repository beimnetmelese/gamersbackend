from django.urls import path, include
from rest_framework.routers import DefaultRouter
from .views import (
    register_user, login_user, logout_user, change_password, telegram_auth_view, get_user_stats,
    UserProfileViewSet, FavoriteViewSet, NotificationViewSet,
    CategoryViewSet, ProductViewSet, GameViewSet, WalletViewSet, PaymentSubmissionViewSet,
    WithdrawalRequestViewSet, UserAdminViewSet, SellerApplicationViewSet,
    ProductDeliveryViewSet, SellerRatingViewSet, ReportViewSet, PlatformSettingViewSet
)

router = DefaultRouter()
router.register(r'profiles', UserProfileViewSet, basename='userprofile')
router.register(r'favorites', FavoriteViewSet, basename='favorite')
router.register(r'notifications', NotificationViewSet, basename='notification')
router.register(r'categories', CategoryViewSet, basename='category')
router.register(r'products', ProductViewSet, basename='product')
router.register(r'games', GameViewSet, basename='game')
router.register(r'wallets', WalletViewSet, basename='wallet')
router.register(r'payments', PaymentSubmissionViewSet, basename='payment')
router.register(r'withdrawals', WithdrawalRequestViewSet, basename='withdrawal')
router.register(r'deliveries', ProductDeliveryViewSet, basename='delivery')
router.register(r'ratings', SellerRatingViewSet, basename='rating')
router.register(r'reports', ReportViewSet, basename='report')
router.register(r'settings', PlatformSettingViewSet, basename='platformsetting')
router.register(r'users', UserAdminViewSet, basename='useradmin')
router.register(r'sellers', SellerApplicationViewSet, basename='sellerapp')

urlpatterns = [
    path('auth/telegram/', telegram_auth_view, name='telegram_auth'),
    path('auth/register/', register_user, name='register_user'),
    path('auth/login/', login_user, name='login_user'),
    path('auth/logout/', logout_user, name='logout_user'),
    path('auth/change_password/', change_password, name='change_password'),
    path('profiles/me/stats/', get_user_stats, name='user_stats'),
    path('', include(router.urls)),
]
