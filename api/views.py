import re
from decimal import Decimal
from rest_framework import viewsets, status, permissions
from rest_framework.decorators import api_view, permission_classes, action
from rest_framework.parsers import MultiPartParser, FormParser, JSONParser
from rest_framework.response import Response
from rest_framework.authtoken.models import Token
from django.contrib.auth import login, logout
from django.contrib.auth.models import User
from django.db import transaction
from django.db.models import Sum, Count, Avg, Q
from django.utils import timezone

from .models import (
    Category, UserProfile, SellerProfile, Product, Game, GameParticipant,
    GameResult, Favorite, Wallet, WalletTransaction, PaymentSubmission,
    WithdrawalRequest, ProductDelivery, Notification, AuditLog,
    SellerRating, Report, PlatformSetting
)
from .serializers import (
    CategorySerializer, UserSerializer, UserProfileSerializer, SellerProfileSerializer,
    ProductSerializer, GameSerializer, GameParticipantSerializer, GameResultSerializer,
    FavoriteSerializer, WalletSerializer, WalletTransactionSerializer,
    PaymentSubmissionSerializer, WithdrawalRequestSerializer, ProductDeliverySerializer,
    NotificationSerializer, AuditLogSerializer,
    SellerRatingSerializer, ReportSerializer, PlatformSettingSerializer
)
from .services import (
    WalletService, PaymentService, WithdrawalService,
    NotificationService, AuthService
)
from .telegram_auth import verify_telegram_init_data, get_or_create_telegram_user
from .engines import resolve_game_winner


def is_telegram_admin(telegram_id) -> bool:
    if not telegram_id:
        return False
    from django.conf import settings
    str_id = str(telegram_id).strip()
    return str_id in [str(aid).strip() for aid in getattr(settings, 'TELEGRAM_ADMIN_IDS', [])]


def is_user_admin(user) -> bool:
    if not user or not user.is_authenticated:
        return False
    if user.is_staff or user.is_superuser:
        return True
    profile = getattr(user, 'profile', None)
    if profile:
        if profile.role == 'ADMIN':
            return True
        if profile.telegram_id and is_telegram_admin(profile.telegram_id):
            return True
    return False


@api_view(['POST'])
@permission_classes([permissions.AllowAny])
def telegram_auth_view(request):
    """
    Authenticates or registers a Telegram user using Telegram initData or Telegram parameters.
    Supports Telegram WebApp HMAC verification with configurable local bypass toggle.
    Lookup & user identification is primarily performed by unique Telegram User ID.
    """
    init_data = request.data.get('initData', request.data.get('init_data', ''))
    telegram_id = request.data.get('telegram_id', request.data.get('id', ''))
    username = request.data.get('username')
    first_name = request.data.get('first_name')
    last_name = request.data.get('last_name')
    photo_url = request.data.get('photo_url')

    if init_data:
        is_valid, parsed_data, msg = verify_telegram_init_data(init_data)
        if not is_valid:
            return Response({'error': msg}, status=status.HTTP_400_BAD_REQUEST)

        user_obj = parsed_data.get('user_obj', {})
        if user_obj:
            if not telegram_id:
                telegram_id = user_obj.get('id')
            if username is None:
                username = user_obj.get('username')
            if first_name is None:
                first_name = user_obj.get('first_name')
            if last_name is None:
                last_name = user_obj.get('last_name')
            if photo_url is None:
                photo_url = user_obj.get('photo_url')

    if not telegram_id:
        return Response({'error': 'Telegram User ID is required for Telegram authentication.'}, status=status.HTTP_400_BAD_REQUEST)

    try:
        user, profile, created = get_or_create_telegram_user(
            telegram_id=str(telegram_id),
            username=username,
            first_name=first_name,
            last_name=last_name,
            photo_url=photo_url
        )
    except Exception as e:
        return Response({'error': f'Failed to process Telegram authentication: {str(e)}'}, status=status.HTTP_400_BAD_REQUEST)

    if not user.is_active:
        return Response({'error': 'Account is suspended or deactivated.'}, status=status.HTTP_403_FORBIDDEN)

    login(request, user)
    wallet = WalletService.get_or_create_wallet(user)
    token, _ = Token.objects.get_or_create(user=user)

    return Response({
        'message': 'Telegram login successful.',
        'created': created,
        'token': token.key,
        'user': UserSerializer(user).data,
        'wallet': WalletSerializer(wallet).data
    }, status=status.HTTP_200_OK if not created else status.HTTP_201_CREATED)


@api_view(['POST'])
@permission_classes([permissions.AllowAny])
def register_user(request):
    username = request.data.get('username', '').strip()
    email = request.data.get('email', '').strip()
    password = request.data.get('password', '')
    role = request.data.get('role', 'USER')

    if not username or not password:
        return Response({'error': 'Username and password are required.'}, status=status.HTTP_400_BAD_REQUEST)

    if User.objects.filter(username__iexact=username).exists():
        return Response({'error': 'Username is already taken.'}, status=status.HTTP_400_BAD_REQUEST)

    if email and User.objects.filter(email__iexact=email).exists():
        return Response({'error': 'Email is already registered.'}, status=status.HTTP_400_BAD_REQUEST)

    with transaction.atomic():
        user = User.objects.create_user(username=username, email=email, password=password)
        profile, _ = UserProfile.objects.get_or_create(user=user)
        # Always initialize newly registered users as 'USER' (Player)
        profile.role = 'USER'
        profile.save()

        # If user requested SELLER role during registration, submit a pending SellerProfile application
        if role == 'SELLER':
            SellerProfile.objects.get_or_create(
                user=user,
                defaults={
                    'business_name': f"{username}'s Store",
                    'phone_number': '+251900000000',
                    'address': 'Addis Ababa',
                    'status': 'PENDING'
                }
            )
            NotificationService.send_notification(
                user=user,
                title="🏪 Seller Application Submitted",
                message="Your request to register as a Seller has been submitted for Admin approval. Your account is active as a Player while waiting for verification.",
                event_type='SYSTEM'
            )

        wallet = WalletService.get_or_create_wallet(user)
        token, _ = Token.objects.get_or_create(user=user)

    login(request, user)
    msg = 'Registration successful! Your request to become a Seller has been submitted for Admin verification.' if role == 'SELLER' else 'Registration successful.'
    return Response({
        'message': msg,
        'token': token.key,
        'user': UserSerializer(user).data,
        'wallet': WalletSerializer(wallet).data
    }, status=status.HTTP_201_CREATED)


@api_view(['POST'])
@permission_classes([permissions.AllowAny])
def login_user(request):
    username_or_email = request.data.get('username_or_email', request.data.get('username', '')).strip()
    password = request.data.get('password', '')

    if not username_or_email or not password:
        return Response({'error': 'Username/email and password are required.'}, status=status.HTTP_400_BAD_REQUEST)

    user = AuthService.authenticate_user(username_or_email, password)
    if not user:
        return Response({'error': 'Invalid username/email or password.'}, status=status.HTTP_401_UNAUTHORIZED)

    if not user.is_active:
        return Response({'error': 'Account is deactivated or suspended.'}, status=status.HTTP_403_FORBIDDEN)

    login(request, user)
    wallet = WalletService.get_or_create_wallet(user)
    token, _ = Token.objects.get_or_create(user=user)

    return Response({
        'message': 'Login successful.',
        'token': token.key,
        'user': UserSerializer(user).data,
        'wallet': WalletSerializer(wallet).data
    })


@api_view(['POST'])
@permission_classes([permissions.IsAuthenticated])
def logout_user(request):
    try:
        request.user.auth_token.delete()
    except Exception:
        pass
    logout(request)
    return Response({'message': 'Logged out successfully.'})


@api_view(['POST'])
@permission_classes([permissions.IsAuthenticated])
def change_password(request):
    user = request.user
    old_password = request.data.get('old_password', '').strip()
    new_password = request.data.get('new_password', '').strip()
    confirm_password = request.data.get('confirm_password', '').strip()

    if not old_password or not new_password or not confirm_password:
        return Response({'error': 'Current password, new password, and confirmation are required.'}, status=status.HTTP_400_BAD_REQUEST)

    if not user.check_password(old_password):
        return Response({'error': 'Incorrect current password.'}, status=status.HTTP_400_BAD_REQUEST)

    if new_password != confirm_password:
        return Response({'error': 'New password and confirm password do not match.'}, status=status.HTTP_400_BAD_REQUEST)

    if len(new_password) < 6:
        return Response({'error': 'New password must be at least 6 characters.'}, status=status.HTTP_400_BAD_REQUEST)

    user.set_password(new_password)
    user.save()
    return Response({'message': 'Password changed successfully!'})



@api_view(['GET'])
@permission_classes([permissions.IsAuthenticated])
def get_user_stats(request):
    user = request.user
    stats = AuthService.get_user_stats(user)
    return Response(stats)


class CategoryViewSet(viewsets.ModelViewSet):
    queryset = Category.objects.all()
    serializer_class = CategorySerializer
    permission_classes = [permissions.AllowAny]


class ProductViewSet(viewsets.ModelViewSet):
    serializer_class = ProductSerializer
    permission_classes = [permissions.IsAuthenticatedOrReadOnly]

    def get_queryset(self):
        user = self.request.user
        if is_user_admin(user):
            return Product.objects.all().select_related('seller', 'seller__user').order_by('-created_at')

        if self.request.query_params.get('my') == 'true' or self.action == 'my_products':
            if user.is_authenticated and hasattr(user, 'seller_profile'):
                return Product.objects.filter(seller=user.seller_profile).order_by('-created_at')
            return Product.objects.none()

        return Product.objects.filter(approval_status='APPROVED').select_related('seller').order_by('-created_at')

    def perform_create(self, serializer):
        user = self.request.user
        if not user.is_authenticated:
            raise permissions.exceptions.NotAuthenticated("Authentication required to create products.")

        if not hasattr(user, 'seller_profile'):
            seller, _ = SellerProfile.objects.get_or_create(
                user=user,
                defaults={
                    'business_name': f"{user.username}'s Store",
                    'phone_number': getattr(getattr(user, 'profile', None), 'phone_number', '') or '+251900000000',
                    'address': 'Addis Ababa',
                    'status': 'PENDING'
                }
            )
        else:
            seller = user.seller_profile

        product = serializer.save(seller=seller, approval_status='PENDING')

        NotificationService.send_notification(
            user=user,
            title="📦 Product Submitted for Approval",
            message=f"Product '{product.title}' has been submitted for Admin verification.",
            event_type='SYSTEM'
        )

    def perform_update(self, serializer):
        user = self.request.user
        instance = serializer.instance
        if not is_user_admin(user) and instance.seller.user != user:
            raise permissions.exceptions.PermissionDenied("You do not have permission to edit this product.")

        if not is_user_admin(user):
            serializer.save(approval_status='PENDING')
        else:
            serializer.save()

    def perform_destroy(self, instance):
        user = self.request.user
        if not is_user_admin(user) and instance.seller.user != user:
            raise permissions.exceptions.PermissionDenied("You do not have permission to delete this product.")

        if instance.games.filter(status__in=['ACTIVE', 'PENDING_APPROVAL']).exists():
            raise permissions.exceptions.ValidationError("Cannot delete product while active or pending competitions exist for it.")

        instance.delete()

    @action(detail=False, methods=['get'], permission_classes=[permissions.IsAuthenticated])
    def my_products(self, request):
        if not hasattr(request.user, 'seller_profile'):
            return Response([])
        products = Product.objects.filter(seller=request.user.seller_profile).order_by('-created_at')
        return Response(ProductSerializer(products, many=True).data)

    @action(detail=True, methods=['post'], permission_classes=[permissions.IsAuthenticated])
    def approve(self, request, pk=None):
        if not is_user_admin(request.user):
            return Response({'error': 'Admin access required.'}, status=status.HTTP_403_FORBIDDEN)

        product = self.get_object()
        product.approval_status = 'APPROVED'
        product.save()

        NotificationService.send_notification(
            user=product.seller.user,
            title="✅ Product Approved!",
            message=f"Your product '{product.title}' has been approved by admin and can now be used in competitions!",
            event_type='SYSTEM'
        )

        AuditLog.objects.create(
            actor=request.user,
            action="APPROVE_PRODUCT",
            target_model="Product",
            details=f"Approved product #{product.id} ('{product.title}') for seller {product.seller.business_name}"
        )

        return Response({'message': f"Product '{product.title}' approved.", 'product': ProductSerializer(product).data})

    @action(detail=True, methods=['post'], permission_classes=[permissions.IsAuthenticated])
    def reject(self, request, pk=None):
        if not is_user_admin(request.user):
            return Response({'error': 'Admin access required.'}, status=status.HTTP_403_FORBIDDEN)

        product = self.get_object()
        reason = request.data.get('reason', 'Product does not meet platform criteria.')
        product.approval_status = 'REJECTED'
        product.save()

        NotificationService.send_notification(
            user=product.seller.user,
            title="❌ Product Rejected",
            message=f"Your product '{product.title}' was rejected by admin. Reason: {reason}",
            event_type='SYSTEM'
        )

        AuditLog.objects.create(
            actor=request.user,
            action="REJECT_PRODUCT",
            target_model="Product",
            details=f"Rejected product #{product.id} ('{product.title}'). Reason: {reason}"
        )

        return Response({'message': f"Product '{product.title}' rejected.", 'product': ProductSerializer(product).data})



class UserProfileViewSet(viewsets.ModelViewSet):
    queryset = UserProfile.objects.all()
    serializer_class = UserProfileSerializer
    permission_classes = [permissions.IsAuthenticated]

    @action(detail=False, methods=['get', 'patch'])
    def me(self, request):
        user = request.user
        profile, _ = UserProfile.objects.get_or_create(user=user)

        if request.method == 'PATCH':
            first_name = request.data.get('first_name', request.data.get('firstName'))
            last_name = request.data.get('last_name', request.data.get('lastName'))
            email = request.data.get('email')
            username = request.data.get('username')
            phone = request.data.get('phone_number', request.data.get('phoneNumber'))
            bio = request.data.get('bio')
            avatar = request.data.get('avatar_url', request.data.get('avatarUrl'))
            notifs = request.data.get('notification_preferences')
            privacy = request.data.get('privacy_settings')
            lang = request.data.get('language')

            user_updated = False
            if first_name is not None:
                clean_fn = str(first_name).strip()
                user.first_name = clean_fn
                profile.telegram_first_name = clean_fn
                user_updated = True

            if last_name is not None:
                user.last_name = str(last_name).strip()
                user_updated = True

            if email is not None:
                clean_email = str(email).strip()
                if clean_email and User.objects.filter(email__iexact=clean_email).exclude(pk=user.pk).exists():
                    return Response({'error': 'Email address is already registered by another account.'}, status=status.HTTP_400_BAD_REQUEST)
                user.email = clean_email
                user_updated = True

            if username is not None and str(username).strip():
                clean_uname = str(username).strip()
                if User.objects.filter(username__iexact=clean_uname).exclude(pk=user.pk).exists():
                    return Response({'error': 'Username is already taken by another account.'}, status=status.HTTP_400_BAD_REQUEST)
                user.username = clean_uname
                user_updated = True

            if user_updated:
                user.save()

            if phone is not None: profile.phone_number = phone
            if bio is not None: profile.bio = bio
            if avatar is not None: profile.avatar_url = avatar
            if notifs is not None: profile.notification_preferences = notifs
            if privacy is not None: profile.privacy_settings = privacy
            if lang is not None: profile.language = lang
            profile.save()

        data = UserProfileSerializer(profile).data
        data['user'] = UserSerializer(user).data
        return Response(data)


class FavoriteViewSet(viewsets.ModelViewSet):
    serializer_class = FavoriteSerializer
    permission_classes = [permissions.IsAuthenticated]

    def get_queryset(self):
        return Favorite.objects.filter(user=self.request.user)

    @action(detail=False, methods=['post'])
    def toggle(self, request):
        user = request.user
        game_id = request.data.get('game_id')
        try:
            game = Game.objects.get(pk=game_id)
        except Game.DoesNotExist:
            return Response({'error': 'Game not found.'}, status=status.HTTP_404_NOT_FOUND)

        fav = Favorite.objects.filter(user=user, game=game).first()
        if fav:
            fav.delete()
            return Response({'is_favorited': False, 'message': 'Removed from favorites.'})
        else:
            Favorite.objects.create(user=user, game=game)
            return Response({'is_favorited': True, 'message': 'Added to favorites.'})


class WalletViewSet(viewsets.ModelViewSet):
    serializer_class = WalletSerializer
    permission_classes = [permissions.IsAuthenticated]

    def get_queryset(self):
        return Wallet.objects.filter(user=self.request.user)

    @action(detail=False, methods=['get'])
    def me(self, request):
        user = request.user
        wallet = WalletService.get_or_create_wallet(user)
        return Response(WalletSerializer(wallet).data)

    @action(detail=False, methods=['get'])
    def history(self, request):
        user = request.user
        wallet = WalletService.get_or_create_wallet(user)
        transactions = WalletTransactionSerializer(wallet.transactions.all().order_by('-created_at'), many=True).data
        deposits = PaymentSubmissionSerializer(PaymentSubmission.objects.filter(user=user).order_by('-submitted_at'), many=True).data
        withdrawals = WithdrawalRequestSerializer(WithdrawalRequest.objects.filter(user=user).order_by('-submitted_at'), many=True).data
        
        return Response({
            'transactions': transactions,
            'deposits': deposits,
            'withdrawals': withdrawals
        })


def clean_decimal_amount(val):
    if val is None:
        return None
    if isinstance(val, (list, tuple)):
        val = val[0] if len(val) > 0 else None
    if val is None:
        return None
    val_str = str(val).strip()
    cleaned = re.sub(r'[^0-9.]', '', val_str)
    if not cleaned or cleaned == '.':
        return None
    try:
        return Decimal(cleaned)
    except Exception:
        return None


class PaymentSubmissionViewSet(viewsets.ModelViewSet):
    serializer_class = PaymentSubmissionSerializer
    permission_classes = [permissions.IsAuthenticated]
    parser_classes = (MultiPartParser, FormParser, JSONParser)

    def get_queryset(self):
        user = self.request.user
        if is_user_admin(user):
            return PaymentSubmission.objects.all().order_by('-submitted_at')
        return PaymentSubmission.objects.filter(user=user).order_by('-submitted_at')

    def create(self, request, *args, **kwargs):
        user = request.user
        payment_method = request.data.get('payment_method', 'Commercial Bank of Ethiopia (CBE)')
        bank = request.data.get('bank', None)
        transaction_id = request.data.get('transaction_id', '')
        amount_raw = request.data.get('amount')
        proof_image = request.FILES.get('proof_image', None)

        amount_dec = clean_decimal_amount(amount_raw)
        if amount_dec is None or amount_dec <= Decimal('0'):
            return Response({'error': 'Invalid amount format. Must be greater than 0 ETB.'}, status=status.HTTP_400_BAD_REQUEST)

        success, msg, deposit, result_data = PaymentService.create_deposit_submission(
            user=user,
            payment_method=payment_method,
            transaction_id=transaction_id,
            amount=amount_dec,
            proof_image=proof_image,
            bank=bank
        )

        if not success and not deposit:
            return Response({'error': msg}, status=status.HTTP_400_BAD_REQUEST)

        return Response({
            'success': success,
            'verified': result_data.get('verified', False),
            'message': msg,
            'submission': PaymentSubmissionSerializer(deposit).data if deposit else None,
            'result_data': result_data
        }, status=status.HTTP_200_OK if success else status.HTTP_400_BAD_REQUEST)

    @action(detail=True, methods=['post'])
    def approve(self, request, pk=None):
        if not is_user_admin(request.user):
            return Response({'error': 'Admin access required.'}, status=status.HTTP_403_FORBIDDEN)

        admin_note = request.data.get('admin_note', 'Approved via Web Admin')

        success, msg, payment = PaymentService.approve_deposit(
            payment_id=pk,
            admin_user=request.user,
            admin_note=admin_note
        )

        if not success:
            return Response({'error': msg}, status=status.HTTP_400_BAD_REQUEST)

        return Response({'message': msg, 'submission': PaymentSubmissionSerializer(payment).data})

    @action(detail=True, methods=['post'])
    def reject(self, request, pk=None):
        if not is_user_admin(request.user):
            return Response({'error': 'Admin access required.'}, status=status.HTTP_403_FORBIDDEN)

        admin_note = request.data.get('admin_note', 'Rejected via Web Admin')

        success, msg, payment = PaymentService.reject_deposit(
            payment_id=pk,
            admin_user=request.user,
            admin_note=admin_note
        )

        if not success:
            return Response({'error': msg}, status=status.HTTP_400_BAD_REQUEST)

        return Response({'message': msg, 'submission': PaymentSubmissionSerializer(payment).data})

    @action(detail=True, methods=['delete', 'post'])
    def delete_log(self, request, pk=None):
        if not is_user_admin(request.user):
            return Response({'error': 'Admin access required.'}, status=status.HTTP_403_FORBIDDEN)

        try:
            deposit = PaymentSubmission.objects.get(pk=pk)
            deposit.delete()
            return Response({'message': f'Deposit log #{pk} deleted successfully.'})
        except PaymentSubmission.DoesNotExist:
            return Response({'error': 'Deposit log not found.'}, status=status.HTTP_404_NOT_FOUND)

    @action(detail=False, methods=['post'])
    def bulk_delete(self, request):
        if not is_user_admin(request.user):
            return Response({'error': 'Admin access required.'}, status=status.HTTP_403_FORBIDDEN)

        ids = request.data.get('ids', [])
        status_filter = request.data.get('status', None)

        if ids:
            deleted_count, _ = PaymentSubmission.objects.filter(id__in=ids).delete()
        elif status_filter:
            deleted_count, _ = PaymentSubmission.objects.filter(status=status_filter).delete()
        else:
            return Response({'error': 'Please provide log IDs or status filter to delete.'}, status=status.HTTP_400_BAD_REQUEST)

        return Response({'message': f'Successfully deleted {deleted_count} deposit log(s).'})


class WithdrawalRequestViewSet(viewsets.ModelViewSet):
    serializer_class = WithdrawalRequestSerializer
    permission_classes = [permissions.IsAuthenticated]
    parser_classes = (MultiPartParser, FormParser, JSONParser)

    def get_queryset(self):
        user = self.request.user
        if is_user_admin(user):
            return WithdrawalRequest.objects.all().order_by('-submitted_at')
        return WithdrawalRequest.objects.filter(user=user).order_by('-submitted_at')

    def create(self, request, *args, **kwargs):
        user = request.user
        withdrawal_method = request.data.get('withdrawal_method', 'Telebirr')
        account_number = request.data.get('account_number', '')
        account_name = request.data.get('account_name', '')
        phone_number = request.data.get('phone_number', '')
        amount_raw = request.data.get('amount')

        amount_dec = clean_decimal_amount(amount_raw)
        if amount_dec is None or amount_dec <= Decimal('0'):
            return Response({'error': 'Invalid amount format.'}, status=status.HTTP_400_BAD_REQUEST)

        success, msg, withdrawal = WithdrawalService.create_withdrawal_request(
            user=user,
            withdrawal_method=withdrawal_method,
            account_number=account_number,
            account_name=account_name,
            phone_number=phone_number,
            amount=amount_dec
        )

        if not success:
            return Response({'error': msg}, status=status.HTTP_400_BAD_REQUEST)

        return Response({
            'message': msg,
            'withdrawal': WithdrawalRequestSerializer(withdrawal).data
        }, status=status.HTTP_201_CREATED)

    @action(detail=True, methods=['post'])
    def approve(self, request, pk=None):
        if not is_user_admin(request.user):
            return Response({'error': 'Admin access required.'}, status=status.HTTP_403_FORBIDDEN)

        admin_note = request.data.get('admin_note', 'Approved via Web Admin')

        success, msg, withdrawal = WithdrawalService.approve_withdrawal(
            withdrawal_id=pk,
            admin_user=request.user,
            admin_note=admin_note
        )

        if not success:
            return Response({'error': msg}, status=status.HTTP_400_BAD_REQUEST)

        return Response({'message': msg, 'withdrawal': WithdrawalRequestSerializer(withdrawal).data})

    @action(detail=True, methods=['post'])
    def reject(self, request, pk=None):
        if not is_user_admin(request.user):
            return Response({'error': 'Admin access required.'}, status=status.HTTP_403_FORBIDDEN)

        admin_note = request.data.get('admin_note', 'Rejected via Web Admin')

        success, msg, withdrawal = WithdrawalService.reject_withdrawal(
            withdrawal_id=pk,
            admin_user=request.user,
            admin_note=admin_note
        )

        if not success:
            return Response({'error': msg}, status=status.HTTP_400_BAD_REQUEST)

        return Response({'message': msg, 'withdrawal': WithdrawalRequestSerializer(withdrawal).data})


class NotificationViewSet(viewsets.ModelViewSet):
    serializer_class = NotificationSerializer
    permission_classes = [permissions.IsAuthenticated]

    def get_queryset(self):
        return Notification.objects.filter(user=self.request.user).order_by('-created_at')

    @action(detail=True, methods=['post'])
    def mark_read(self, request, pk=None):
        notif = self.get_object()
        notif.is_read = True
        notif.save()
        return Response({'status': 'marked as read'})

    @action(detail=False, methods=['post'])
    def mark_all_read(self, request):
        Notification.objects.filter(user=request.user, is_read=False).update(is_read=True)
        return Response({'status': 'all marked as read'})


class GameViewSet(viewsets.ModelViewSet):
    queryset = Game.objects.all().order_by('-created_at')
    serializer_class = GameSerializer
    permission_classes = [permissions.AllowAny]

    def perform_create(self, serializer):
        user = self.request.user
        seller = getattr(user, 'seller_profile', None)
        if not seller and user.is_authenticated:
            seller, _ = SellerProfile.objects.get_or_create(
                user=user,
                defaults={
                    'business_name': f"{user.username}'s Store",
                    'phone_number': getattr(getattr(user, 'profile', None), 'phone_number', '') or '+251900000000',
                    'address': 'Addis Ababa',
                    'status': 'PENDING'
                }
            )
        prod = serializer.validated_data.get('product')
        if prod and not is_user_admin(user) and seller and prod.seller != seller:
            raise permissions.exceptions.PermissionDenied("You can only create competitions for products in your own inventory.")
        serializer.save(seller=seller, status='PENDING_APPROVAL')

    @action(detail=False, methods=['get'], permission_classes=[permissions.IsAuthenticated])
    def seller_games(self, request):
        user = request.user
        seller = getattr(user, 'seller_profile', None)
        if not seller:
            return Response([])
        games = Game.objects.filter(seller=seller).select_related('product', 'seller').order_by('-created_at')
        return Response(GameSerializer(games, many=True, context={'request': request}).data)

    @action(detail=False, methods=['get'], permission_classes=[permissions.IsAuthenticated])
    def my_games(self, request):
        user = request.user
        filter_type = request.query_params.get('status', 'ALL').upper()
        
        # User's participants entries
        participants = GameParticipant.objects.filter(user=user).select_related('game', 'game__product')
        games_list = [p.game for p in participants]

        if filter_type == 'ACTIVE':
            games_list = [g for g in games_list if g.status == 'ACTIVE']
        elif filter_type == 'UPCOMING':
            games_list = [g for g in games_list if g.status in ['APPROVED', 'PENDING_APPROVAL']]
        elif filter_type == 'COMPLETED':
            games_list = [g for g in games_list if g.status == 'COMPLETED']
        elif filter_type == 'WON':
            won_game_ids = GameResult.objects.filter(winner=user).values_list('game_id', flat=True)
            games_list = [g for g in games_list if g.id in won_game_ids]
        elif filter_type == 'LOST':
            won_game_ids = GameResult.objects.filter(winner=user).values_list('game_id', flat=True)
            games_list = [g for g in games_list if g.status == 'COMPLETED' and g.id not in won_game_ids]
        elif filter_type == 'CANCELLED':
            games_list = [g for g in games_list if g.status in ['CANCELLED', 'REFUNDED']]

        serializer = GameSerializer(games_list, many=True, context={'request': request})
        return Response(serializer.data)

    @action(detail=True, methods=['post'], permission_classes=[permissions.IsAuthenticated])
    def join_game(self, request, pk=None):
        game = self.get_object()
        user = request.user

        if game.is_bidding_ended() or game.status != 'ACTIVE':
            return Response({'error': 'Game is no longer accepting entries.'}, status=status.HTTP_400_BAD_REQUEST)

        sel_box = request.data.get('selected_box')
        sel_num = request.data.get('selected_number')
        sel_card = request.data.get('selected_card')
        pred_ans = request.data.get('prediction_answer')
        timer_delta = request.data.get('timer_delta_ms')

        with transaction.atomic():
            wallet = Wallet.objects.select_for_update().get(user=user)

            if sel_box is not None and GameParticipant.objects.filter(game=game, selected_box=sel_box).exists():
                return Response({'error': f'Box #{sel_box} is already taken by another player!'}, status=status.HTTP_400_BAD_REQUEST)

            # Prevent duplicate submission if user submits duplicate choice
            existing_duplicate = GameParticipant.objects.filter(
                game=game, user=user,
                selected_box=sel_box, selected_number=sel_num, selected_card=sel_card
            ).first()
            if existing_duplicate and (sel_box is not None or sel_num is not None or sel_card is not None):
                return Response({'error': 'You have already submitted this choice for this competition!'}, status=status.HTTP_400_BAD_REQUEST)

            success, msg, _ = WalletService.deduct_game_entry(user, game, note=f"Joined {game.title}")
            if not success:
                return Response({'error': msg}, status=status.HTTP_400_BAD_REQUEST)

            participant = GameParticipant.objects.create(
                game=game,
                user=user,
                selected_box=sel_box,
                selected_number=sel_num,
                selected_card=sel_card,
                prediction_answer=pred_ans,
                timer_delta_ms=timer_delta
            )

        return Response({
            'message': 'Successfully joined game!',
            'participant': GameParticipantSerializer(participant).data
        }, status=status.HTTP_201_CREATED)

    @action(detail=True, methods=['post'], permission_classes=[permissions.IsAuthenticated])
    def approve_game(self, request, pk=None):
        if not is_user_admin(request.user):
            return Response({'error': 'Admin access required.'}, status=status.HTTP_403_FORBIDDEN)
        game = self.get_object()
        game.status = 'ACTIVE'
        game.rejection_reason = ''
        game.save()
        return Response({'message': 'Game approved and activated.', 'game': GameSerializer(game).data})

    @action(detail=True, methods=['post'], permission_classes=[permissions.IsAuthenticated])
    def reject_game(self, request, pk=None):
        if not is_user_admin(request.user):
            return Response({'error': 'Admin access required.'}, status=status.HTTP_403_FORBIDDEN)
        game = self.get_object()
        reason = request.data.get('reason', 'Rejected by Admin')
        game.status = 'REJECTED'
        game.rejection_reason = reason
        game.save()
        return Response({'message': 'Game rejected.', 'game': GameSerializer(game).data})

    @action(detail=True, methods=['post'], permission_classes=[permissions.IsAuthenticated])
    def resolve_game(self, request, pk=None):
        if not is_user_admin(request.user):
            return Response({'error': 'Admin access required.'}, status=status.HTTP_403_FORBIDDEN)
        game = self.get_object()
        result, msg = resolve_game_winner(game)
        return Response({
            'message': msg,
            'result': GameResultSerializer(result).data if result else None
        })


class UserAdminViewSet(viewsets.ViewSet):
    permission_classes = [permissions.IsAuthenticated]

    def list(self, request):
        if not is_user_admin(request.user):
            return Response({'error': 'Admin access required.'}, status=status.HTTP_403_FORBIDDEN)
        
        users = User.objects.all().select_related('profile').order_by('-date_joined')
        data = []
        for u in users:
            prof = getattr(u, 'profile', None)
            data.append({
                'id': u.id,
                'username': u.username,
                'email': u.email,
                'role': prof.role if prof else ('ADMIN' if u.is_staff else 'USER'),
                'account_status': prof.account_status if prof else ('ACTIVE' if u.is_active else 'SUSPENDED'),
                'is_active': u.is_active,
                'date_joined': u.date_joined,
                'phone_number': prof.phone_number if prof else '',
                'ban_reason': prof.ban_reason if prof else '',
                'banned_at': prof.banned_at if prof else None,
            })
        return Response(data)

    @action(detail=True, methods=['post'])
    def toggle_status(self, request, pk=None):
        if not is_user_admin(request.user):
            return Response({'error': 'Admin access required.'}, status=status.HTTP_403_FORBIDDEN)

        try:
            target_user = User.objects.get(pk=pk)
        except User.DoesNotExist:
            return Response({'error': 'User not found.'}, status=status.HTTP_404_NOT_FOUND)

        new_status = request.data.get('status', 'ACTIVE') # ACTIVE, SUSPENDED, BANNED
        profile, _ = UserProfile.objects.get_or_create(user=target_user)
        profile.account_status = new_status
        profile.save()

        target_user.is_active = (new_status == 'ACTIVE')
        target_user.save()

        AuditLog.objects.create(
            actor=request.user,
            action="UPDATE_USER_STATUS",
            target_model="User",
            details=f"Updated status of {target_user.username} to {new_status}"
        )

        return Response({'message': f'User {target_user.username} status set to {new_status}.'})

    @action(detail=True, methods=['post'])
    def change_role(self, request, pk=None):
        if not is_user_admin(request.user):
            return Response({'error': 'Admin access required.'}, status=status.HTTP_403_FORBIDDEN)

        try:
            target_user = User.objects.get(pk=pk)
        except User.DoesNotExist:
            return Response({'error': 'User not found.'}, status=status.HTTP_404_NOT_FOUND)

        new_role = request.data.get('role', 'USER') # USER, SELLER, ADMIN
        profile, _ = UserProfile.objects.get_or_create(user=target_user)
        profile.role = new_role
        profile.save()

        if new_role == 'ADMIN':
            target_user.is_staff = True
            target_user.save()

        AuditLog.objects.create(
            actor=request.user,
            action="CHANGE_USER_ROLE",
            target_model="User",
            details=f"Changed role of {target_user.username} to {new_role}"
        )

        return Response({'message': f'User {target_user.username} role updated to {new_role}.'})

    @action(detail=True, methods=['post'])
    def ban(self, request, pk=None):
        if not is_user_admin(request.user):
            return Response({'error': 'Admin access required.'}, status=status.HTTP_403_FORBIDDEN)

        try:
            target_user = User.objects.get(pk=pk)
        except User.DoesNotExist:
            return Response({'error': 'User not found.'}, status=status.HTTP_404_NOT_FOUND)

        if target_user == request.user:
            return Response({'error': 'You cannot ban your own administrator account.'}, status=status.HTTP_400_BAD_REQUEST)

        if target_user.is_superuser or (target_user.is_staff and not request.user.is_superuser):
            return Response({'error': 'Cannot ban a staff administrator or superuser account.'}, status=status.HTTP_400_BAD_REQUEST)

        reason = request.data.get('reason', 'Violation of platform policies.')
        profile, _ = UserProfile.objects.get_or_create(user=target_user)
        profile.account_status = 'BANNED'
        profile.ban_reason = reason
        profile.banned_at = timezone.now()
        profile.save()

        target_user.is_active = False
        target_user.save()

        AuditLog.objects.create(
            actor=request.user,
            action="BAN_USER",
            target_model="User",
            details=f"Banned user {target_user.username}. Reason: {reason}"
        )

        NotificationService.send_notification(
            user=target_user,
            title="🚫 Account Banned",
            message=f"Your account has been restricted by platform administration. Reason: {reason}",
            event_type='SYSTEM'
        )

        return Response({'message': f'User {target_user.username} has been banned.', 'ban_reason': reason})

    @action(detail=True, methods=['post'])
    def unban(self, request, pk=None):
        if not is_user_admin(request.user):
            return Response({'error': 'Admin access required.'}, status=status.HTTP_403_FORBIDDEN)

        try:
            target_user = User.objects.get(pk=pk)
        except User.DoesNotExist:
            return Response({'error': 'User not found.'}, status=status.HTTP_404_NOT_FOUND)

        profile, _ = UserProfile.objects.get_or_create(user=target_user)
        profile.account_status = 'ACTIVE'
        profile.ban_reason = ''
        profile.banned_at = None
        profile.save()

        target_user.is_active = True
        target_user.save()

        AuditLog.objects.create(
            actor=request.user,
            action="UNBAN_USER",
            target_model="User",
            details=f"Unbanned user {target_user.username}"
        )

        NotificationService.send_notification(
            user=target_user,
            title="✅ Account Unbanned",
            message="Your account restriction has been lifted. You may now resume using the platform.",
            event_type='SYSTEM'
        )

        return Response({'message': f'User {target_user.username} has been unbanned.'})

    @action(detail=False, methods=['get'])
    def analytics(self, request):
        if not is_user_admin(request.user):
            return Response({'error': 'Admin access required.'}, status=status.HTTP_403_FORBIDDEN)

        total_users = User.objects.count()
        active_users = User.objects.filter(is_active=True).count()
        banned_users = UserProfile.objects.filter(account_status='BANNED').count()
        verified_sellers = SellerProfile.objects.filter(status='VERIFIED').count()
        pending_sellers = SellerProfile.objects.filter(status='PENDING').count()

        total_games = Game.objects.count()
        active_games = Game.objects.filter(status='ACTIVE').count()
        completed_games = Game.objects.filter(status='COMPLETED').count()
        pending_games = Game.objects.filter(status='PENDING_APPROVAL').count()

        total_products = Product.objects.count()
        approved_products = Product.objects.filter(approval_status='APPROVED').count()
        pending_products = Product.objects.filter(approval_status='PENDING').count()
        rejected_products = Product.objects.filter(approval_status='REJECTED').count()

        total_deposits_val = PaymentSubmission.objects.filter(status='APPROVED').aggregate(total=Sum('amount'))['total'] or Decimal('0.00')
        pending_deposits_count = PaymentSubmission.objects.filter(status='PENDING').count()

        total_withdrawals_val = WithdrawalRequest.objects.filter(status='APPROVED').aggregate(total=Sum('amount'))['total'] or Decimal('0.00')
        pending_withdrawals_count = WithdrawalRequest.objects.filter(status='PENDING').count()

        total_entries = GameParticipant.objects.count()
        platform_volume = GameParticipant.objects.aggregate(total=Sum('game__entry_fee'))['total'] or Decimal('0.00')

        total_deliveries = ProductDelivery.objects.count()
        pending_deliveries = ProductDelivery.objects.filter(status__in=['PREPARING', 'SHIPPED', 'OUT_FOR_DELIVERY']).count()
        completed_deliveries = ProductDelivery.objects.filter(status__in=['DELIVERED', 'CONFIRMED']).count()

        pending_reports = Report.objects.filter(status='PENDING').count()

        return Response({
            'users': {
                'total': total_users,
                'active': active_users,
                'banned': banned_users,
                'verified_sellers': verified_sellers,
                'pending_sellers': pending_sellers,
            },
            'competitions': {
                'total': total_games,
                'active': active_games,
                'completed': completed_games,
                'pending_approval': pending_games,
                'total_entries': total_entries,
            },
            'products': {
                'total': total_products,
                'approved': approved_products,
                'pending': pending_products,
                'rejected': rejected_products,
            },
            'financials': {
                'total_deposits_etb': float(total_deposits_val),
                'pending_deposits_count': pending_deposits_count,
                'total_withdrawals_etb': float(total_withdrawals_val),
                'pending_withdrawals_count': pending_withdrawals_count,
                'platform_volume_etb': float(platform_volume),
            },
            'fulfillment': {
                'total_deliveries': total_deliveries,
                'pending_deliveries': pending_deliveries,
                'completed_deliveries': completed_deliveries,
            },
            'moderation': {
                'pending_reports': pending_reports,
            }
        })


class SellerApplicationViewSet(viewsets.ViewSet):
    permission_classes = [permissions.IsAuthenticated]

    @action(detail=False, methods=['get', 'patch'])
    def me(self, request):
        user = request.user
        seller = getattr(user, 'seller_profile', None)
        if not seller:
            return Response({'error': 'Seller profile not found. Please register as a seller first.'}, status=status.HTTP_404_NOT_FOUND)

        if request.method == 'PATCH':
            b_name = request.data.get('business_name')
            phone = request.data.get('phone_number')
            address = request.data.get('address')
            desc = request.data.get('description')
            if b_name is not None and b_name.strip(): seller.business_name = b_name.strip()
            if phone is not None and phone.strip(): seller.phone_number = phone.strip()
            if address is not None and address.strip(): seller.address = address.strip()
            if desc is not None: seller.description = desc.strip()
            seller.save()

        return Response(SellerProfileSerializer(seller).data)

    @action(detail=False, methods=['get'])
    def stats(self, request):
        user = request.user
        seller = getattr(user, 'seller_profile', None)
        if not seller:
            return Response({
                'total_products': 0,
                'active_products': 0,
                'total_games': 0,
                'active_games': 0,
                'completed_games': 0,
                'total_revenue_etb': 0.0,
                'pending_deliveries': 0,
                'completed_deliveries': 0,
                'average_rating': 0.0,
                'rating_count': 0,
                'wallet_balance': 0.0,
            })

        total_prods = Product.objects.filter(seller=seller).count()
        active_prods = Product.objects.filter(seller=seller, approval_status='APPROVED').count()

        games_qs = Game.objects.filter(seller=seller)
        total_games = games_qs.count()
        active_games = games_qs.filter(status='ACTIVE').count()
        completed_games = games_qs.filter(status='COMPLETED').count()

        total_rev = GameParticipant.objects.filter(game__seller=seller).aggregate(total=Sum('game__entry_fee'))['total'] or Decimal('0.00')

        pending_del = ProductDelivery.objects.filter(seller=seller, status__in=['PREPARING', 'SHIPPED', 'OUT_FOR_DELIVERY']).count()
        completed_del = ProductDelivery.objects.filter(seller=seller, status__in=['DELIVERED', 'CONFIRMED']).count()

        rating_agg = SellerRating.objects.filter(seller=seller).aggregate(avg=Avg('rating'), count=Count('id'))
        avg_rating = round(float(rating_agg['avg'] or 0.0), 1)
        rating_count = rating_agg['count'] or 0

        wallet = getattr(user, 'wallet', None)
        wallet_bal = float(wallet.balance) if wallet else 0.0

        return Response({
            'total_products': total_prods,
            'active_products': active_prods,
            'total_games': total_games,
            'active_games': active_games,
            'completed_games': completed_games,
            'total_revenue_etb': float(total_rev),
            'pending_deliveries': pending_del,
            'completed_deliveries': completed_del,
            'average_rating': avg_rating,
            'rating_count': rating_count,
            'wallet_balance': wallet_bal,
        })

    @action(detail=False, methods=['get'])
    def analytics(self, request):
        user = request.user
        seller = getattr(user, 'seller_profile', None)
        if not seller:
            return Response({'error': 'Seller profile not found.'}, status=status.HTTP_404_NOT_FOUND)

        # Games breakdown
        games = Game.objects.filter(seller=seller).select_related('product').order_by('-created_at')[:20]
        games_data = []
        for g in games:
            part_count = g.participants.count()
            collected = float(g.entry_fee * part_count)
            games_data.append({
                'id': g.id,
                'title': g.title,
                'product_title': g.product.title if g.product else '',
                'status': g.status,
                'participants_count': part_count,
                'max_participants': g.max_participants,
                'collected_etb': collected,
                'created_at': g.created_at,
            })

        # Rating breakdown
        ratings = SellerRating.objects.filter(seller=seller).select_related('user')
        breakdown = {1: 0, 2: 0, 3: 0, 4: 0, 5: 0}
        recent_reviews = []
        for r in ratings:
            breakdown[r.rating] = breakdown.get(r.rating, 0) + 1
            if len(recent_reviews) < 10:
                recent_reviews.append({
                    'id': r.id,
                    'username': r.user.username,
                    'rating': r.rating,
                    'review': r.review,
                    'created_at': r.created_at,
                })

        return Response({
            'games_performance': games_data,
            'ratings_breakdown': breakdown,
            'recent_reviews': recent_reviews,
        })

    @action(detail=False, methods=['post'])
    def apply(self, request):
        user = request.user
        business_name = request.data.get('business_name', '').strip()
        phone_number = request.data.get('phone_number', '').strip()
        address = request.data.get('address', '').strip()
        description = request.data.get('description', '').strip()

        if not business_name or not phone_number or not address:
            return Response({'error': 'Business name, phone number, and location address are required.'}, status=status.HTTP_400_BAD_REQUEST)

        seller_profile, created = SellerProfile.objects.get_or_create(
            user=user,
            defaults={
                'business_name': business_name,
                'phone_number': phone_number,
                'address': address,
                'description': description,
                'status': 'PENDING'
            }
        )

        if not created:
            seller_profile.business_name = business_name
            seller_profile.phone_number = phone_number
            seller_profile.address = address
            seller_profile.description = description
            seller_profile.status = 'PENDING'
            seller_profile.save()

        NotificationService.send_notification(
            user=user,
            title="🏪 Seller Application Submitted",
            message=f"Your request to register '{business_name}' as a verified seller has been submitted. Waiting for admin review.",
            event_type='SYSTEM'
        )

        return Response({
            'message': 'Seller application submitted successfully! Pending Admin verification.',
            'seller': SellerProfileSerializer(seller_profile).data
        }, status=status.HTTP_201_CREATED)

    @action(detail=False, methods=['get'])
    def pending(self, request):
        if not is_user_admin(request.user):
            return Response({'error': 'Admin access required.'}, status=status.HTTP_403_FORBIDDEN)
        
        pending_sellers = SellerProfile.objects.filter(status='PENDING').order_by('-created_at')
        return Response(SellerProfileSerializer(pending_sellers, many=True).data)

    @action(detail=True, methods=['post'])
    def approve(self, request, pk=None):
        if not is_user_admin(request.user):
            return Response({'error': 'Admin access required.'}, status=status.HTTP_403_FORBIDDEN)

        try:
            seller = SellerProfile.objects.get(pk=pk)
        except SellerProfile.DoesNotExist:
            return Response({'error': 'Seller request not found.'}, status=status.HTTP_404_NOT_FOUND)

        seller.status = 'VERIFIED'
        seller.verified_at = timezone.now()
        seller.save()

        profile, _ = UserProfile.objects.get_or_create(user=seller.user)
        profile.role = 'SELLER'
        profile.save()

        NotificationService.send_notification(
            user=seller.user,
            title="🎉 Seller Application Approved!",
            message=f"Your seller application for '{seller.business_name}' was approved! Seller features unlocked.",
            event_type='SYSTEM'
        )

        AuditLog.objects.create(
            actor=request.user,
            action="APPROVE_SELLER",
            target_model="SellerProfile",
            details=f"Approved seller application #{seller.id} ({seller.business_name}) for user {seller.user.username}"
        )

        return Response({'message': f'Seller profile for {seller.business_name} approved and role set to SELLER.'})

    @action(detail=True, methods=['post'])
    def reject(self, request, pk=None):
        if not is_user_admin(request.user):
            return Response({'error': 'Admin access required.'}, status=status.HTTP_403_FORBIDDEN)

        try:
            seller = SellerProfile.objects.get(pk=pk)
        except SellerProfile.DoesNotExist:
            return Response({'error': 'Seller request not found.'}, status=status.HTTP_404_NOT_FOUND)

        reason = request.data.get('reason', 'Rejected by Admin')
        seller.status = 'REJECTED'
        seller.save()

        NotificationService.send_notification(
            user=seller.user,
            title="❌ Seller Application Rejected",
            message=f"Your seller application for '{seller.business_name}' was rejected. Reason: {reason}",
            event_type='SYSTEM'
        )

        AuditLog.objects.create(
            actor=request.user,
            action="REJECT_SELLER",
            target_model="SellerProfile",
            details=f"Rejected seller application #{seller.id} for user {seller.user.username}"
        )

        return Response({'message': 'Seller application rejected.'})


class ProductDeliveryViewSet(viewsets.ModelViewSet):
    serializer_class = ProductDeliverySerializer
    permission_classes = [permissions.IsAuthenticated]

    def get_queryset(self):
        user = self.request.user
        if is_user_admin(user):
            return ProductDelivery.objects.all().select_related(
                'game_result__game', 'game_result__game__product', 'winner', 'seller'
            ).order_by('-created_at')

        seller = getattr(user, 'seller_profile', None)
        if seller:
            return ProductDelivery.objects.filter(Q(seller=seller) | Q(winner=user)).select_related(
                'game_result__game', 'game_result__game__product', 'winner', 'seller'
            ).order_by('-created_at')

        return ProductDelivery.objects.filter(winner=user).select_related(
            'game_result__game', 'game_result__game__product', 'winner', 'seller'
        ).order_by('-created_at')

    @action(detail=True, methods=['post'])
    def update_status(self, request, pk=None):
        delivery = self.get_object()
        user = request.user
        is_admin = is_user_admin(user)
        is_seller = (delivery.seller.user == user)
        is_winner = (delivery.winner == user)

        new_status = request.data.get('status')
        valid_statuses = [c[0] for c in ProductDelivery.STATUS_CHOICES]
        if new_status and new_status not in valid_statuses:
            return Response({'error': f'Invalid status. Must be one of {valid_statuses}'}, status=status.HTTP_400_BAD_REQUEST)

        # Winner can confirm delivery receipt
        if is_winner and not (is_admin or is_seller):
            if new_status != 'CONFIRMED':
                return Response({'error': 'Winners can only confirm receipt of delivered packages.'}, status=status.HTTP_403_FORBIDDEN)
            if delivery.status != 'DELIVERED':
                return Response({'error': 'Cannot confirm receipt before package is marked as delivered.'}, status=status.HTTP_400_BAD_REQUEST)
        elif not (is_admin or is_seller):
            return Response({'error': 'Only the seller, winner, or admin can update delivery tracking status.'}, status=status.HTTP_403_FORBIDDEN)

        # Seller cannot confirm receipt on winner's behalf
        if is_seller and not is_admin and new_status == 'CONFIRMED':
            return Response({'error': 'Only the recipient winner can confirm delivery receipt.'}, status=status.HTTP_403_FORBIDDEN)

        # Seller cannot revert status backwards
        status_order = {'PREPARING': 1, 'SHIPPED': 2, 'OUT_FOR_DELIVERY': 3, 'DELIVERED': 4, 'CONFIRMED': 5}
        if is_seller and not is_admin and new_status:
            current_rank = status_order.get(delivery.status, 0)
            new_rank = status_order.get(new_status, 0)
            if new_rank < current_rank:
                return Response({'error': f'Cannot revert status backwards from {delivery.status} to {new_status}.'}, status=status.HTTP_400_BAD_REQUEST)

        if new_status:
            delivery.status = new_status

        tracking = request.data.get('tracking_code')
        if tracking is not None:
            delivery.tracking_code = tracking.strip()

        delivery.save()

        status_messages = {
            'PREPARING': 'Your competition prize package is being prepared for dispatch.',
            'SHIPPED': f"Your prize has been shipped! Tracking code: {delivery.tracking_code or 'N/A'}",
            'OUT_FOR_DELIVERY': 'Your prize package is out for delivery today!',
            'DELIVERED': 'Your prize has been delivered. Please confirm receipt!',
            'CONFIRMED': 'Delivery successfully confirmed by recipient.',
        }

        NotificationService.send_notification(
            user=delivery.winner,
            title=f"📦 Delivery Update: {delivery.get_status_display()}",
            message=status_messages.get(delivery.status, f"Delivery status changed to {delivery.status}."),
            event_type='SYSTEM'
        )

        AuditLog.objects.create(
            actor=user,
            action="UPDATE_DELIVERY_STATUS",
            target_model="ProductDelivery",
            details=f"Updated Delivery #{delivery.id} status to {delivery.status}, tracking: {delivery.tracking_code}"
        )

        return Response({'message': 'Delivery updated successfully.', 'delivery': ProductDeliverySerializer(delivery).data})

    @action(detail=True, methods=['post'])
    def update_address(self, request, pk=None):
        delivery = self.get_object()
        user = request.user
        if delivery.winner != user and not is_user_admin(user):
            return Response({'error': 'Only the winner can update the shipping address.'}, status=status.HTTP_403_FORBIDDEN)

        if delivery.status in ['DELIVERED', 'CONFIRMED']:
            return Response({'error': 'Cannot change address on delivered packages.'}, status=status.HTTP_400_BAD_REQUEST)

        address = request.data.get('delivery_address')
        phone = request.data.get('phone_number')

        if address: delivery.delivery_address = address.strip()
        if phone: delivery.phone_number = phone.strip()
        delivery.save()

        return Response({'message': 'Address updated.', 'delivery': ProductDeliverySerializer(delivery).data})


class SellerRatingViewSet(viewsets.ModelViewSet):
    serializer_class = SellerRatingSerializer
    permission_classes = [permissions.IsAuthenticatedOrReadOnly]

    def get_queryset(self):
        seller_id = self.request.query_params.get('seller_id')
        if seller_id:
            return SellerRating.objects.filter(seller_id=seller_id).select_related('user', 'seller')
        return SellerRating.objects.all().select_related('user', 'seller')

    def perform_create(self, serializer):
        user = self.request.user
        seller_id = self.request.data.get('seller')
        try:
            seller = SellerProfile.objects.get(pk=seller_id)
        except SellerProfile.DoesNotExist:
            raise permissions.exceptions.ValidationError({'error': 'Seller not found.'})

        if seller.user == user:
            raise permissions.exceptions.ValidationError({'error': 'Sellers cannot rate their own store.'})

        game_id = self.request.data.get('game')
        game = Game.objects.filter(pk=game_id).first() if game_id else None

        serializer.save(user=user, seller=seller, game=game)


class ReportViewSet(viewsets.ModelViewSet):
    serializer_class = ReportSerializer
    permission_classes = [permissions.IsAuthenticated]

    def get_queryset(self):
        user = self.request.user
        if is_user_admin(user):
            return Report.objects.all().select_related('reporter', 'moderator').order_by('-created_at')
        return Report.objects.filter(reporter=user).order_by('-created_at')

    def perform_create(self, serializer):
        serializer.save(reporter=self.request.user, status='PENDING')

    @action(detail=True, methods=['post'])
    def resolve(self, request, pk=None):
        if not is_user_admin(request.user):
            return Response({'error': 'Admin access required.'}, status=status.HTTP_403_FORBIDDEN)

        report = self.get_object()
        resolution_note = request.data.get('resolution_note', 'Resolved by platform moderator.')
        new_status = request.data.get('status', 'RESOLVED')
        action_taken = request.data.get('action_taken', 'NO_ACTION')

        report.status = new_status
        report.moderator = request.user
        report.resolution_note = resolution_note
        report.resolved_at = timezone.now()
        report.save()

        if action_taken == 'BAN_USER':
            target_user = None
            if report.target_type == 'USER':
                target_user = User.objects.filter(pk=report.target_id).first()
            elif report.target_type == 'SELLER':
                sp = SellerProfile.objects.filter(pk=report.target_id).first()
                if sp: target_user = sp.user

            if target_user:
                target_user.is_active = False
                target_user.save()
                prof, _ = UserProfile.objects.get_or_create(user=target_user)
                prof.account_status = 'BANNED'
                prof.ban_reason = f"Banned via Report #{report.id}: {resolution_note}"
                prof.banned_at = timezone.now()
                prof.save()

        AuditLog.objects.create(
            actor=request.user,
            action="RESOLVE_REPORT",
            target_model="Report",
            details=f"Resolved Report #{report.id} ({report.target_type}) - Action: {action_taken}"
        )

        return Response({'message': f'Report #{report.id} has been {new_status.lower()}.', 'report': ReportSerializer(report).data})


class PlatformSettingViewSet(viewsets.ViewSet):
    permission_classes = [permissions.IsAuthenticated]

    def list(self, request):
        settings = PlatformSetting.objects.all()
        if not settings.exists():
            default_settings = [
                ('PLATFORM_COMMISSION_PERCENT', '5.0', 'Platform commission taken from game pools (%)'),
                ('MIN_WITHDRAWAL_ETB', '100.0', 'Minimum withdrawal amount in ETB'),
                ('MAX_WITHDRAWAL_ETB', '50000.0', 'Maximum daily withdrawal amount in ETB'),
                ('MIN_DEPOSIT_ETB', '50.0', 'Minimum deposit allowed in ETB'),
                ('AUTO_APPROVE_VERIFIED_SELLERS_PRODUCTS', 'false', 'Automatically approve products added by verified sellers'),
                ('MAINTENANCE_MODE', 'false', 'Put platform into maintenance mode'),
                ('SUPPORT_PHONE', '+251 900 123 456', 'Customer support hotline phone number'),
                ('SUPPORT_EMAIL', 'support@gamersplatform.et', 'Customer support official email'),
            ]
            for key, val, desc in default_settings:
                PlatformSetting.objects.get_or_create(key=key, defaults={'value': val, 'description': desc})
            settings = PlatformSetting.objects.all()

        return Response(PlatformSettingSerializer(settings, many=True).data)

    @action(detail=False, methods=['post'])
    def update_settings(self, request):
        if not is_user_admin(request.user):
            return Response({'error': 'Admin access required.'}, status=status.HTTP_403_FORBIDDEN)

        items = request.data.get('settings', request.data)
        if isinstance(items, str):
            import json
            try:
                items = json.loads(items)
            except Exception:
                pass

        if isinstance(items, dict):
            for k, v in items.items():
                if k != 'settings':
                    PlatformSetting.objects.update_or_create(key=k, defaults={'value': str(v)})
        elif isinstance(items, list):
            for item in items:
                k = item.get('key')
                v = item.get('value')
                desc = item.get('description', '')
                if k is not None and v is not None:
                    obj, _ = PlatformSetting.objects.update_or_create(key=k, defaults={'value': str(v)})
                    if desc:
                        obj.description = desc
                        obj.save()

        AuditLog.objects.create(
            actor=request.user,
            action="UPDATE_PLATFORM_SETTINGS",
            target_model="PlatformSetting",
            details=f"Admin {request.user.username} updated platform settings."
        )

        all_settings = PlatformSetting.objects.all()
        return Response({'message': 'Settings updated successfully.', 'settings': PlatformSettingSerializer(all_settings, many=True).data})


