from rest_framework import serializers
from django.contrib.auth.models import User
from .models import (
    UserProfile, SellerProfile, Product, Game, GameParticipant,
    GameResult, Wallet, WalletTransaction, PaymentSubmission,
    ProductDelivery, Notification, AuditLog
)

class UserSerializer(serializers.ModelSerializer):
    role = serializers.CharField(source='profile.role', read_only=True)
    account_status = serializers.CharField(source='profile.account_status', read_only=True)

    class Meta:
        model = User
        fields = ['id', 'username', 'email', 'first_name', 'last_name', 'role', 'account_status']


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


class GameSerializer(serializers.ModelSerializer):
    product_details = ProductSerializer(source='product', read_only=True)
    seller_name = serializers.CharField(source='seller.business_name', read_only=True)
    participants_count = serializers.IntegerField(source='participants.count', read_only=True)
    result = GameResultSerializer(read_only=True)

    class Meta:
        model = Game
        fields = '__all__'


class WalletTransactionSerializer(serializers.ModelSerializer):
    class Meta:
        model = WalletTransaction
        fields = '__all__'


class WalletSerializer(serializers.ModelSerializer):
    username = serializers.CharField(source='user.username', read_only=True)
    transactions = WalletTransactionSerializer(many=True, read_only=True)

    class Meta:
        model = Wallet
        fields = ['id', 'user', 'username', 'balance', 'updated_at', 'transactions']


class PaymentSubmissionSerializer(serializers.ModelSerializer):
    username = serializers.CharField(source='user.username', read_only=True)

    class Meta:
        model = PaymentSubmission
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
