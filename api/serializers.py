from rest_framework import serializers
from django.contrib.auth.models import User
from .models import (
    Category, UserProfile, SellerProfile, Product, Game, GameParticipant,
    GameResult, Favorite, Wallet, WalletTransaction, PaymentSubmission,
    WithdrawalRequest, ProductDelivery, Notification, AuditLog
)

class CategorySerializer(serializers.ModelSerializer):
    class Meta:
        model = Category
        fields = '__all__'


class UserSerializer(serializers.ModelSerializer):
    role = serializers.CharField(source='profile.role', read_only=True)
    account_status = serializers.CharField(source='profile.account_status', read_only=True)
    phone_number = serializers.CharField(source='profile.phone_number', read_only=True)
    avatar_url = serializers.CharField(source='profile.avatar_url', read_only=True)

    class Meta:
        model = User
        fields = ['id', 'username', 'email', 'first_name', 'last_name', 'role', 'account_status', 'phone_number', 'avatar_url']


class UserProfileSerializer(serializers.ModelSerializer):
    username = serializers.CharField(source='user.username', read_only=True)
    email = serializers.CharField(source='user.email', read_only=True)

    class Meta:
        model = UserProfile
        fields = '__all__'


class SellerProfileSerializer(serializers.ModelSerializer):
    username = serializers.CharField(source='user.username', read_only=True)

    class Meta:
        model = SellerProfile
        fields = '__all__'


class ProductSerializer(serializers.ModelSerializer):
    seller_name = serializers.CharField(source='seller.business_name', read_only=True)

    class Meta:
        model = Product
        fields = '__all__'


class GameParticipantSerializer(serializers.ModelSerializer):
    username = serializers.CharField(source='user.username', read_only=True)

    class Meta:
        model = GameParticipant
        fields = '__all__'


class GameResultSerializer(serializers.ModelSerializer):
    winner_name = serializers.CharField(source='winner.username', read_only=True)

    class Meta:
        model = GameResult
        fields = '__all__'


class FavoriteSerializer(serializers.ModelSerializer):
    game_title = serializers.CharField(source='game.title', read_only=True)
    product_image = serializers.CharField(source='game.product.image_url', read_only=True)
    entry_fee = serializers.DecimalField(source='game.entry_fee', max_digits=10, decimal_places=2, read_only=True)
    game_type = serializers.CharField(source='game.game_type', read_only=True)
    status = serializers.CharField(source='game.status', read_only=True)

    class Meta:
        model = Favorite
        fields = '__all__'


class GameSerializer(serializers.ModelSerializer):
    product_details = ProductSerializer(source='product', read_only=True)
    seller_name = serializers.CharField(source='seller.business_name', read_only=True)
    participants_count = serializers.IntegerField(source='participants.count', read_only=True)
    participants = GameParticipantSerializer(many=True, read_only=True)
    result = GameResultSerializer(read_only=True)
    is_favorited = serializers.SerializerMethodField(read_only=True)

    class Meta:
        model = Game
        fields = '__all__'

    def get_is_favorited(self, obj) -> bool:
        request = self.context.get('request')
        if request and request.user and request.user.is_authenticated:
            return Favorite.objects.filter(user=request.user, game=obj).exists()
        return False


class WalletTransactionSerializer(serializers.ModelSerializer):
    class Meta:
        model = WalletTransaction
        fields = '__all__'


class WalletSerializer(serializers.ModelSerializer):
    username = serializers.CharField(source='user.username', read_only=True)
    available_balance = serializers.DecimalField(max_digits=12, decimal_places=2, read_only=True)
    transactions = WalletTransactionSerializer(many=True, read_only=True)

    class Meta:
        model = Wallet
        fields = ['id', 'user', 'username', 'balance', 'reserved_balance', 'available_balance', 'updated_at', 'transactions']


class PaymentSubmissionSerializer(serializers.ModelSerializer):
    username = serializers.CharField(source='user.username', read_only=True)

    class Meta:
        model = PaymentSubmission
        fields = '__all__'


class WithdrawalRequestSerializer(serializers.ModelSerializer):
    username = serializers.CharField(source='user.username', read_only=True)

    class Meta:
        model = WithdrawalRequest
        fields = '__all__'


class ProductDeliverySerializer(serializers.ModelSerializer):
    winner_name = serializers.CharField(source='winner.username', read_only=True)
    seller_name = serializers.CharField(source='seller.business_name', read_only=True)
    game_title = serializers.CharField(source='game_result.game.title', read_only=True)

    class Meta:
        model = ProductDelivery
        fields = '__all__'


class NotificationSerializer(serializers.ModelSerializer):
    class Meta:
        model = Notification
        fields = '__all__'


class AuditLogSerializer(serializers.ModelSerializer):
    actor_name = serializers.CharField(source='actor.username', read_only=True)

    class Meta:
        model = AuditLog
        fields = '__all__'
