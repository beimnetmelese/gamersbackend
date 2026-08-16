from django.urls import path, include
from rest_framework.routers import DefaultRouter
from .views import (
    register_user, UserProfileViewSet, SellerProfileViewSet,
    ProductViewSet, GameViewSet, WalletViewSet, PaymentSubmissionViewSet,
    ProductDeliveryViewSet, platform_analytics
)

router = DefaultRouter()
router.register(r'profiles', UserProfileViewSet)
router.register(r'sellers', SellerProfileViewSet)
router.register(r'products', ProductViewSet)
router.register(r'games', GameViewSet)
router.register(r'wallets', WalletViewSet)
router.register(r'payments', PaymentSubmissionViewSet)
router.register(r'deliveries', ProductDeliveryViewSet)

urlpatterns = [
    path('auth/register/', register_user, name='register_user'),
    path('analytics/', platform_analytics, name='platform_analytics'),
    path('', include(router.urls)),
]
