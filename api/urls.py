from django.urls import path, include
from rest_framework.routers import DefaultRouter
from .views import (
    register_user, login_user, logout_user, change_password, get_user_stats,
    UserProfileViewSet, FavoriteViewSet, NotificationViewSet,
    CategoryViewSet, GameViewSet, WalletViewSet, PaymentSubmissionViewSet,
    WithdrawalRequestViewSet, UserAdminViewSet, SellerApplicationViewSet
)

router = DefaultRouter()
router.register(r'profiles', UserProfileViewSet, basename='userprofile')
router.register(r'favorites', FavoriteViewSet, basename='favorite')
router.register(r'notifications', NotificationViewSet, basename='notification')
router.register(r'categories', CategoryViewSet, basename='category')
router.register(r'games', GameViewSet, basename='game')
router.register(r'wallets', WalletViewSet, basename='wallet')
router.register(r'payments', PaymentSubmissionViewSet, basename='payment')
router.register(r'withdrawals', WithdrawalRequestViewSet, basename='withdrawal')
router.register(r'users', UserAdminViewSet, basename='useradmin')
router.register(r'sellers', SellerApplicationViewSet, basename='sellerapp')

urlpatterns = [
    path('auth/register/', register_user, name='register_user'),
    path('auth/login/', login_user, name='login_user'),
    path('auth/logout/', logout_user, name='logout_user'),
    path('auth/change_password/', change_password, name='change_password'),
    path('profiles/me/stats/', get_user_stats, name='user_stats'),
    path('', include(router.urls)),
]
