from rest_framework import serializers
from django.contrib.auth.models import User
from .models import (
    Category, UserProfile, SellerProfile, Product, Game, GameParticipant,
    GameResult, Favorite, Wallet, WalletTransaction, PaymentSubmission,
    PaymentVerificationLog, WithdrawalRequest, ProductDelivery, Notification, AuditLog,
    SellerRating, Report, PlatformSetting
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
    telegram_id = serializers.CharField(source='profile.telegram_id', read_only=True)
    telegram_username = serializers.CharField(source='profile.telegram_username', read_only=True)
    telegram_first_name = serializers.CharField(source='profile.telegram_first_name', read_only=True)
    ban_reason = serializers.CharField(source='profile.ban_reason', read_only=True)
    banned_at = serializers.DateTimeField(source='profile.banned_at', read_only=True)

    class Meta:
        model = User
        fields = [
            'id', 'username', 'email', 'first_name', 'last_name', 'role',
            'account_status', 'phone_number', 'avatar_url', 'telegram_id',
            'telegram_username', 'telegram_first_name', 'ban_reason', 'banned_at'
        ]


class UserProfileSerializer(serializers.ModelSerializer):
    username = serializers.CharField(source='user.username', read_only=True)
    email = serializers.CharField(source='user.email', read_only=True)

    class Meta:
        model = UserProfile
        fields = '__all__'


class SellerProfileSerializer(serializers.ModelSerializer):
    username = serializers.CharField(source='user.username', read_only=True)
    rating_average = serializers.SerializerMethodField(read_only=True)
    ratings_count = serializers.SerializerMethodField(read_only=True)

    class Meta:
        model = SellerProfile
        fields = '__all__'

    def get_rating_average(self, obj) -> float:
        ratings = obj.ratings.all()
        if not ratings.exists():
            return 5.0
        from django.db.models import Avg
        avg = ratings.aggregate(Avg('rating'))['rating__avg']
        return round(float(avg), 1) if avg else 5.0

    def get_ratings_count(self, obj) -> int:
        return obj.ratings.count()


class ProductSerializer(serializers.ModelSerializer):
    seller_name = serializers.CharField(source='seller.business_name', read_only=True)
    seller_id = serializers.IntegerField(source='seller.id', read_only=True)
    games_count = serializers.IntegerField(source='games.count', read_only=True)
    image_url = serializers.CharField(required=False, allow_blank=True, default='')

    class Meta:
        model = Product
        fields = '__all__'
        read_only_fields = ['seller', 'approval_status', 'created_at']


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
        read_only_fields = ['seller', 'views_count', 'created_at']

    def to_internal_value(self, data):
        data_copy = data.copy() if hasattr(data, 'copy') else dict(data)
        prod_val = data_copy.get('product')
        if isinstance(prod_val, dict):
            prod_id = prod_val.get('id')
            if prod_id and Product.objects.filter(pk=prod_id).exists():
                data_copy['product'] = prod_id
            else:
                req = self.context.get('request')
                seller = getattr(req.user, 'seller_profile', None) if req and req.user and req.user.is_authenticated else None
                if seller:
                    new_prod = Product.objects.create(
                        seller=seller,
                        title=prod_val.get('title', 'Competition Product'),
                        category=prod_val.get('category', 'Electronics'),
                        description=prod_val.get('description', ''),
                        image_url=prod_val.get('imageUrl') or prod_val.get('image_url', ''),
                        condition=prod_val.get('condition', 'NEW'),
                        estimated_value=prod_val.get('estimatedValue') or prod_val.get('estimated_value', 1000),
                        location=prod_val.get('location', 'Addis Ababa'),
                        approval_status='PENDING'
                    )
                    data_copy['product'] = new_prod.id

        return super().to_internal_value(data_copy)

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


class PaymentVerificationLogSerializer(serializers.ModelSerializer):
    username = serializers.CharField(source='user.username', read_only=True)

    class Meta:
        model = PaymentVerificationLog
        fields = '__all__'


class PaymentSubmissionSerializer(serializers.ModelSerializer):
    username = serializers.CharField(source='user.username', read_only=True)
    user_email = serializers.CharField(source='user.email', read_only=True)
    user_phone = serializers.CharField(source='user.profile.phone_number', read_only=True)
    wallet_balance = serializers.DecimalField(source='user.wallet.balance', max_digits=12, decimal_places=2, read_only=True)
    verification_logs = PaymentVerificationLogSerializer(many=True, read_only=True)

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
    seller_phone = serializers.CharField(source='seller.phone_number', read_only=True)
    game_title = serializers.CharField(source='game_result.game.title', read_only=True)
    product_title = serializers.CharField(source='game_result.game.product.title', read_only=True)
    product_image = serializers.CharField(source='game_result.game.product.image_url', read_only=True)

    class Meta:
        model = ProductDelivery
        fields = '__all__'
        read_only_fields = ['game_result', 'winner', 'seller', 'created_at', 'updated_at']


class NotificationSerializer(serializers.ModelSerializer):
    class Meta:
        model = Notification
        fields = '__all__'


class AuditLogSerializer(serializers.ModelSerializer):
    actor_name = serializers.CharField(source='actor.username', read_only=True)

    class Meta:
        model = AuditLog
        fields = '__all__'


class SellerRatingSerializer(serializers.ModelSerializer):
    user_username = serializers.CharField(source='user.username', read_only=True)
    seller_name = serializers.CharField(source='seller.business_name', read_only=True)

    class Meta:
        model = SellerRating
        fields = '__all__'
        read_only_fields = ['user', 'created_at']

    def validate_rating(self, value):
        if value < 1 or value > 5:
            raise serializers.ValidationError("Rating must be an integer between 1 and 5.")
        return value


class ReportSerializer(serializers.ModelSerializer):
    reporter_username = serializers.CharField(source='reporter.username', read_only=True)
    moderator_username = serializers.CharField(source='moderator.username', read_only=True)

    class Meta:
        model = Report
        fields = '__all__'
        read_only_fields = ['reporter', 'moderator', 'created_at', 'resolved_at']


class PlatformSettingSerializer(serializers.ModelSerializer):
    class Meta:
        model = PlatformSetting
        fields = '__all__'
        read_only_fields = ['updated_at']

