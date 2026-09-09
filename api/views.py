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

from .models import (
    Category, UserProfile, SellerProfile, Product, Game, GameParticipant,
    GameResult, Favorite, Wallet, WalletTransaction, PaymentSubmission,
    WithdrawalRequest, ProductDelivery, Notification, AuditLog
)
from .serializers import (
    CategorySerializer, UserSerializer, UserProfileSerializer, SellerProfileSerializer,
    ProductSerializer, GameSerializer, GameParticipantSerializer, GameResultSerializer,
    FavoriteSerializer, WalletSerializer, WalletTransactionSerializer,
    PaymentSubmissionSerializer, WithdrawalRequestSerializer, ProductDeliverySerializer,
    NotificationSerializer, AuditLogSerializer
)
from .services import (
    WalletService, PaymentService, WithdrawalService,
    NotificationService, AuthService
)
from .engines import resolve_game_winner


def is_user_admin(user) -> bool:
    if not user or not user.is_authenticated:
        return False
    if user.is_staff or user.is_superuser:
        return True
    return hasattr(user, 'profile') and user.profile.role == 'ADMIN'


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


class UserProfileViewSet(viewsets.ModelViewSet):
    queryset = UserProfile.objects.all()
    serializer_class = UserProfileSerializer
    permission_classes = [permissions.IsAuthenticated]

    @action(detail=False, methods=['get', 'patch'])
    def me(self, request):
        user = request.user
        profile, _ = UserProfile.objects.get_or_create(user=user)

        if request.method == 'PATCH':
            phone = request.data.get('phone_number')
            bio = request.data.get('bio')
            avatar = request.data.get('avatar_url')
            notifs = request.data.get('notification_preferences')
            privacy = request.data.get('privacy_settings')
            lang = request.data.get('language')

            if phone is not None: profile.phone_number = phone
            if bio is not None: profile.bio = bio
            if avatar is not None: profile.avatar_url = avatar
            if notifs is not None: profile.notification_preferences = notifs
            if privacy is not None: profile.privacy_settings = privacy
            if lang is not None: profile.language = lang
            profile.save()

            if 'username' in request.data and request.data['username'].strip():
                new_uname = request.data['username'].strip()
                if not User.objects.filter(username__iexact=new_uname).exclude(pk=user.pk).exists():
                    user.username = new_uname
                    user.save()

        return Response(UserProfileSerializer(profile).data)


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
        payment_method = request.data.get('payment_method', 'Telebirr')
        transaction_id = request.data.get('transaction_id', '')
        amount_raw = request.data.get('amount')
        proof_image = request.FILES.get('proof_image', None)

        amount_dec = clean_decimal_amount(amount_raw)
        if amount_dec is None or amount_dec <= Decimal('0'):
            return Response({'error': 'Invalid amount format. Must be greater than 0 ETB.'}, status=status.HTTP_400_BAD_REQUEST)

        success, msg, deposit = PaymentService.create_deposit_submission(
            user=user,
            payment_method=payment_method,
            transaction_id=transaction_id,
            amount=amount_dec,
            proof_image=proof_image
        )

        if not success:
            return Response({'error': msg}, status=status.HTTP_400_BAD_REQUEST)

        return Response({
            'message': msg,
            'submission': PaymentSubmissionSerializer(deposit).data
        }, status=status.HTTP_201_CREATED)

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

        with db_transaction.atomic():
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
                'phone_number': prof.phone_number if prof else ''
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


class SellerApplicationViewSet(viewsets.ViewSet):
    permission_classes = [permissions.IsAuthenticated]

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
        from django.utils import timezone
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

