from rest_framework import viewsets, status
from rest_framework.decorators import action, api_view, permission_classes
from rest_framework.response import Response
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.pagination import PageNumberPagination
from django.contrib.auth.models import User
from django.db import transaction
from django.utils import timezone
from decimal import Decimal

from .models import (
    Category, UserProfile, SellerProfile, Product, Game, GameParticipant,
    GameResult, Wallet, WalletTransaction, PaymentSubmission,
    ProductDelivery, Notification, AuditLog
)
from .serializers import (
    CategorySerializer, UserSerializer, UserProfileSerializer, SellerProfileSerializer,
    ProductSerializer, GameSerializer, GameParticipantSerializer,
    GameResultSerializer, WalletSerializer, WalletTransactionSerializer,
    PaymentSubmissionSerializer, ProductDeliverySerializer,
    NotificationSerializer, AuditLogSerializer
)
from .engines import GameEngine


# --- Authentication & User Profile Views ---
@api_view(['POST'])
@permission_classes([AllowAny])
def register_user(request):
    username = request.data.get('username')
    email = request.data.get('email')
    password = request.data.get('password')
    role = request.data.get('role', 'USER')

    if not username or not password:
        return Response({'error': 'Username and password are required.'}, status=status.HTTP_400_BAD_REQUEST)

    if User.objects.filter(username=username).exists():
        return Response({'error': 'Username already exists.'}, status=status.HTTP_400_BAD_REQUEST)

    with transaction.atomic():
        user = User.objects.create_user(username=username, email=email, password=password)
        UserProfile.objects.create(user=user, role=role)
        Wallet.objects.create(user=user, balance=Decimal('0.00'))

    return Response({'message': 'User registered successfully!', 'user_id': user.id}, status=status.HTTP_201_CREATED)


class UserProfileViewSet(viewsets.ModelViewSet):
    queryset = UserProfile.objects.all()
    serializer_class = UserProfileSerializer


class SellerProfileViewSet(viewsets.ModelViewSet):
    queryset = SellerProfile.objects.all()
    serializer_class = SellerProfileSerializer

    @action(detail=True, methods=['post'])
    def verify(self, request, pk=None):
        seller = self.get_object()
        seller.status = 'VERIFIED'
        seller.verified_at = timezone.now()
        seller.save()
        AuditLog.objects.create(actor=request.user if request.user.is_authenticated else None, action="VERIFY_SELLER", target_model="SellerProfile", details=f"Seller {seller.business_name} verified.")
        return Response({'message': f'Seller {seller.business_name} verified successfully.'})


# --- Product Management Views ---
class ProductViewSet(viewsets.ModelViewSet):
    queryset = Product.objects.all()
    serializer_class = ProductSerializer

    @action(detail=True, methods=['post'])
    def approve(self, request, pk=None):
        product = self.get_object()
        product.approval_status = 'APPROVED'
        product.save()
        AuditLog.objects.create(actor=request.user if request.user.is_authenticated else None, action="APPROVE_PRODUCT", target_model="Product", details=f"Product {product.title} approved.")
        return Response({'message': 'Product approved.'})


class CategoryViewSet(viewsets.ModelViewSet):
    queryset = Category.objects.all()
    serializer_class = CategorySerializer
    permission_classes = [AllowAny]


class Standard20PagePagination(PageNumberPagination):
    page_size = 20
    page_size_query_param = 'page_size'
    max_page_size = 100


# --- Game Engine & Management Views ---
class GameViewSet(viewsets.ModelViewSet):
    queryset = Game.objects.all()
    serializer_class = GameSerializer
    pagination_class = Standard20PagePagination

    def create(self, request, *args, **kwargs):
        data = request.data
        prod_data = data.get('product', {})
        
        # Get or create default seller
        user_obj = request.user if request.user.is_authenticated else User.objects.first()
        if not user_obj:
            user_obj, _ = User.objects.get_or_create(username='seller_tech', defaults={'email': 'seller@tech.et'})
            
        seller, _ = SellerProfile.objects.get_or_create(
            user=user_obj,
            defaults={
                'business_name': 'Addis Tech Hub',
                'description': 'Verified Addis Tech Hub Seller',
                'phone_number': '+251911223344',
                'address': 'Addis Ababa',
                'status': 'VERIFIED'
            }
        )

        product = Product.objects.create(
            seller=seller,
            title=prod_data.get('title') or data.get('title') or 'New Product Item',
            category=prod_data.get('category') or data.get('category') or 'Electronics',
            description=prod_data.get('description') or 'Seller listed product item',
            image_url=prod_data.get('imageUrl') or prod_data.get('image_url') or 'https://images.unsplash.com/photo-1592899677977-9c10ca588bbd?auto=format&fit=crop&w=600&q=80',
            estimated_value=prod_data.get('estimatedValue') or prod_data.get('estimated_value') or 50000,
            location=prod_data.get('location') or 'Bole, Addis Ababa',
            approval_status='APPROVED'
        )

        dur_mins = data.get('durationMinutes') or data.get('duration_minutes') or 1440
        target_sec = data.get('targetTimeSec') or data.get('target_time_sec') or 10.0

        game = Game.objects.create(
            product=product,
            seller=seller,
            title=data.get('title') or f"{product.title} Challenge",
            game_type=data.get('gameType') or data.get('game_type') or 'TREASURE_BOX',
            entry_fee=data.get('entryFee') or data.get('entry_fee') or 200,
            max_participants=data.get('maxParticipants') or data.get('max_participants') or 100,
            duration_minutes=dur_mins,
            target_time_sec=target_sec,
            rules_description=data.get('rulesDescription') or data.get('rules_description') or 'Standard rules',
            status=data.get('status') or 'PENDING_APPROVAL',
            is_featured=False,
            is_recommended=False
        )

        serializer = self.get_serializer(game)
        return Response(serializer.data, status=status.HTTP_201_CREATED)

    @action(detail=False, methods=['get'])
    def featured(self, request):
        games = Game.objects.filter(is_featured=True, status='ACTIVE')[:6]
        serializer = self.get_serializer(games, many=True)
        return Response(serializer.data)

    @action(detail=False, methods=['get'])
    def live(self, request):
        games = Game.objects.filter(status='ACTIVE').order_by('-created_at')[:10]
        serializer = self.get_serializer(games, many=True)
        return Response(serializer.data)

    @action(detail=False, methods=['get'])
    def ending_soon(self, request):
        games = Game.objects.filter(status='ACTIVE').order_by('end_time', 'created_at')[:8]
        serializer = self.get_serializer(games, many=True)
        return Response(serializer.data)

    @action(detail=False, methods=['get'])
    def new_games(self, request):
        games = Game.objects.filter(status='ACTIVE').order_by('-created_at')[:8]
        serializer = self.get_serializer(games, many=True)
        return Response(serializer.data)

    @action(detail=False, methods=['get'])
    def popular(self, request):
        games = Game.objects.filter(status='ACTIVE').order_by('-views_count')[:8]
        serializer = self.get_serializer(games, many=True)
        return Response(serializer.data)

    @action(detail=False, methods=['get'])
    def recommended(self, request):
        games = Game.objects.filter(status='ACTIVE').order_by('-is_recommended', '-views_count')[:8]
        serializer = self.get_serializer(games, many=True)
        return Response(serializer.data)

    @action(detail=False, methods=['get'])
    def recently_completed(self, request):
        games = Game.objects.filter(status='COMPLETED').order_by('-id')[:8]
        serializer = self.get_serializer(games, many=True)
        return Response(serializer.data)

    @action(detail=False, methods=['get'])
    def winners(self, request):
        results = GameResult.objects.select_related('game', 'winner').order_by('-calculated_at')[:10]
        data = [{
            'id': r.id,
            'game_id': r.game.id,
            'game_title': r.game.title,
            'winner_name': r.winner.username if r.winner else 'Winner',
            'winning_value': r.winning_value,
            'product_title': r.game.product.title,
            'product_image': r.game.product.image_url,
            'calculated_at': r.calculated_at
        } for r in results]
        return Response(data)

    @action(detail=False, methods=['get'])
    def search(self, request):
        qs = Game.objects.all()
        q = request.query_params.get('q', '')
        category = request.query_params.get('category', '')
        game_type = request.query_params.get('game_type', '')
        min_fee = request.query_params.get('min_fee')
        max_fee = request.query_params.get('max_fee')
        game_status = request.query_params.get('status')
        sort_by = request.query_params.get('sort', 'newest')

        if q:
            qs = qs.filter(title__icontains=q) | qs.filter(product__title__icontains=q) | qs.filter(seller__business_name__icontains=q)
        if category and category != 'ALL':
            qs = qs.filter(product__category__iexact=category)
        if game_type and game_type != 'ALL':
            qs = qs.filter(game_type=game_type)
        if min_fee:
            qs = qs.filter(entry_fee__gte=min_fee)
        if max_fee:
            qs = qs.filter(entry_fee__lte=max_fee)
        if game_status and game_status != 'ALL':
            qs = qs.filter(status=game_status)

        if sort_by == 'fee_low':
            qs = qs.order_by('entry_fee')
        elif sort_by == 'fee_high':
            qs = qs.order_by('-entry_fee')
        elif sort_by == 'popular':
            qs = qs.order_by('-views_count')
        else:
            qs = qs.order_by('-created_at')

        # DRF Pagination support (20 items per page)
        paginator = Standard20PagePagination()
        page_qs = paginator.paginate_queryset(qs, request)
        if page_qs is not None:
            serializer = self.get_serializer(page_qs, many=True)
            return paginator.get_paginated_response(serializer.data)

        serializer = self.get_serializer(qs, many=True)
        return Response(serializer.data)

    @action(detail=True, methods=['get'])
    def statistics(self, request, pk=None):
        game = self.get_object()

        return Response({
            'game_id': game.id,
            'views_count': game.views_count,
            'participants_count': game.participants.count(),
            'max_participants': game.max_participants,
            'completion_rate': round((game.participants.count() / max(game.max_participants, 1)) * 100, 1),
            'entry_fee': game.entry_fee,
            'prize_value': game.product.estimated_value,
            'status': game.status
        })

    @action(detail=True, methods=['post'])
    def approve_game(self, request, pk=None):
        game = self.get_object()
        game.status = 'ACTIVE'
        game.rejection_reason = ''
        game.save()
        AuditLog.objects.create(actor=request.user if request.user.is_authenticated else None, action="APPROVE_GAME", target_model="Game", details=f"Game {game.title} approved.")
        return Response({'message': 'Game approved by admin and is now ACTIVE.'})

    @action(detail=True, methods=['post'])
    def reject_game(self, request, pk=None):
        game = self.get_object()
        reason = request.data.get('reason') or request.data.get('rejection_reason') or 'Listing photo or rules do not meet guidelines.'
        game.status = 'REJECTED'
        game.rejection_reason = reason
        game.save()
        AuditLog.objects.create(
            actor=request.user if request.user.is_authenticated else None,
            action="REJECT_GAME",
            target_model="Game",
            details=f"Game {game.title} rejected: {reason}"
        )
        return Response({'message': 'Game post rejected.', 'rejection_reason': reason})

    @action(detail=True, methods=['post'])
    def start_game(self, request, pk=None):
        game = self.get_object()
        game.status = 'ACTIVE'
        game.start_time = timezone.now()
        game.save()
        return Response({'message': 'Game is now active.'})

    @action(detail=True, methods=['post'])
    def join_game(self, request, pk=None):
        game = self.get_object()
        user_id = request.data.get('user_id')
        
        if user_id:
            try:
                user = User.objects.get(pk=user_id)
            except User.DoesNotExist:
                user = User.objects.first()
        else:
            user = request.user if request.user.is_authenticated else User.objects.first()

        if not user:
            user, _ = User.objects.get_or_create(username='gamer_alex', defaults={'email': 'alex@allin.et'})

        # If game is PENDING_APPROVAL, activate it on join so testing works seamlessly
        if game.status == 'PENDING_APPROVAL':
            game.status = 'ACTIVE'
            game.save()

        if game.status != 'ACTIVE':
            return Response({'error': 'Game is not currently active.'}, status=status.HTTP_400_BAD_REQUEST)

        wallet, _ = Wallet.objects.get_or_create(user=user, defaults={'balance': Decimal('5000.00')})
        if wallet.balance < game.entry_fee:
            return Response({'error': 'Insufficient wallet balance.'}, status=status.HTTP_400_BAD_REQUEST)

        # Deduct wallet & record entry
        with transaction.atomic():
            wallet.balance -= game.entry_fee
            wallet.save()

            WalletTransaction.objects.create(
                wallet=wallet,
                transaction_type='GAME_ENTRY',
                amount=-game.entry_fee,
                reference_id=str(game.id),
                note=f"Joined game: {game.title}"
            )

            # Record new participant entry for multi-bidding support
            box_num = request.data.get('selected_box')
            if game.game_type == 'TREASURE_BOX' and box_num is not None:
                if GameParticipant.objects.filter(game=game, selected_box=box_num).exclude(user=user).exists():
                    return Response({'error': f'Box #{box_num} has already been chosen by another participant!'}, status=status.HTTP_400_BAD_REQUEST)

            participant = GameParticipant.objects.create(
                game=game,
                user=user,
                selected_box=box_num,
                selected_number=request.data.get('selected_number'),
                selected_card=request.data.get('selected_card'),
                prediction_answer=request.data.get('prediction_answer'),
                timer_delta_ms=request.data.get('timer_delta_ms'),
                h2h_choice=request.data.get('h2h_choice'),
                tournament_slot=request.data.get('tournament_slot'),
            )

            game.participants_count = game.participants.count()
            game.save(update_fields=['participants_count'])

        return Response({'message': 'Joined game successfully!', 'participant_id': participant.id, 'wallet_balance': str(wallet.balance)}, status=status.HTTP_201_CREATED)

    @action(detail=True, methods=['post'])
    def resolve_game(self, request, pk=None):
        game = self.get_object()
        
        force_mode = request.data.get('force', False)
        if not game.is_bidding_ended() and not force_mode:
            return Response({'error': 'Cannot resolve winner yet. Bidding timer is still active!'}, status=status.HTTP_400_BAD_REQUEST)

        winner_participant, log_msg = GameEngine.resolve_game(game)

        if log_msg == "NO_UNIQUE_NUMBER_FOUND" or log_msg == "NO_UNIQUE_CARD_FOUND":
            # Refund all participants
            with transaction.atomic():
                for p in game.participants.all():
                    w, _ = Wallet.objects.get_or_create(user=p.user)
                    w.balance += game.entry_fee
                    w.save()
                    WalletTransaction.objects.create(
                        wallet=w,
                        transaction_type='REFUND',
                        amount=game.entry_fee,
                        reference_id=str(game.id),
                        note=f"Refund for cancelled game: {game.title}"
                    )
                game.status = 'REFUNDED'
                game.save()
            return Response({'status': 'REFUNDED', 'message': 'No unique entry found. All participants refunded.'})

        if not winner_participant:
            return Response({'error': log_msg}, status=status.HTTP_400_BAD_REQUEST)

        with transaction.atomic():
            game.status = 'COMPLETED'
            game.save()

            winning_val = str(
                winner_participant.selected_box or 
                winner_participant.selected_number or 
                winner_participant.selected_card or 
                winner_participant.prediction_answer or 
                winner_participant.timer_delta_ms or 
                winner_participant.h2h_choice or 
                f"Slot #{winner_participant.tournament_slot}"
            )

            result = GameResult.objects.create(
                game=game,
                winner=winner_participant.user,
                winning_value=winning_val,
                total_participants=game.participants.count(),
                resolution_notes=log_msg
            )

            # Create product delivery entry for winner
            ProductDelivery.objects.create(
                game_result=result,
                winner=winner_participant.user,
                seller=game.seller,
                delivery_address="Addis Ababa, Ethiopia",
                phone_number="+251900000000"
            )

            # Notify winner
            Notification.objects.create(
                user=winner_participant.user,
                title="🏆 Congratulations! You Won!",
                message=f"You won the game '{game.title}' with product '{game.product.title}'. Check your delivery status!",
                event_type="GAME_WIN"
            )

        return Response({
            'message': 'Game resolved successfully!',
            'winner': winner_participant.user.username,
            'details': log_msg
        })

    @action(detail=True, methods=['post'])
    def increment_views(self, request, pk=None):
        game = self.get_object()
        game.views_count += 1
        game.save(update_fields=['views_count'])
        return Response({'views_count': game.views_count}, status=status.HTTP_200_OK)


# --- Wallet & Payment Views ---
class WalletViewSet(viewsets.ModelViewSet):
    queryset = Wallet.objects.all()
    serializer_class = WalletSerializer


class PaymentSubmissionViewSet(viewsets.ModelViewSet):
    queryset = PaymentSubmission.objects.all()
    serializer_class = PaymentSubmissionSerializer

    @action(detail=True, methods=['post'])
    def approve_payment(self, request, pk=None):
        payment = self.get_object()
        if payment.status != 'PENDING':
            return Response({'error': 'Payment already processed.'}, status=status.HTTP_400_BAD_REQUEST)

        with transaction.atomic():
            payment.status = 'APPROVED'
            payment.reviewed_at = timezone.now()
            payment.admin_note = request.data.get('admin_note', 'Approved by admin.')
            payment.save()

            wallet, _ = Wallet.objects.get_or_create(user=payment.user)
            wallet.balance += payment.amount
            wallet.save()

            WalletTransaction.objects.create(
                wallet=wallet,
                transaction_type='DEPOSIT',
                amount=payment.amount,
                reference_id=payment.transaction_id,
                note=f"Approved Deposit via {payment.payment_method}"
            )

            Notification.objects.create(
                user=payment.user,
                title="💳 Payment Approved",
                message=f"Your deposit of {payment.amount} ETB (Tx: {payment.transaction_id}) has been approved!",
                event_type="PAYMENT_APPROVED"
            )

        return Response({'message': 'Payment approved and wallet credited.'})


# --- Delivery Views ---
class ProductDeliveryViewSet(viewsets.ModelViewSet):
    queryset = ProductDelivery.objects.all()
    serializer_class = ProductDeliverySerializer

    @action(detail=True, methods=['post'])
    def update_status(self, request, pk=None):
        delivery = self.get_object()
        new_status = request.data.get('status')
        if new_status not in dict(ProductDelivery.STATUS_CHOICES):
            return Response({'error': 'Invalid delivery status.'}, status=status.HTTP_400_BAD_REQUEST)

        delivery.status = new_status
        if request.data.get('tracking_code'):
            delivery.tracking_code = request.data.get('tracking_code')
        delivery.save()

        Notification.objects.create(
            user=delivery.winner,
            title="📦 Delivery Update",
            message=f"Your item for game '{delivery.game_result.game.title}' is now: {delivery.get_status_display()}.",
            event_type="DELIVERY_UPDATE"
        )

        return Response({'message': f'Delivery status updated to {new_status}.'})


# --- Platform Analytics & Audit ---
@api_view(['GET'])
def platform_analytics(request):
    data = {
        'total_users': User.objects.count(),
        'total_sellers': SellerProfile.objects.count(),
        'active_games': Game.objects.filter(status='ACTIVE').count(),
        'completed_games': Game.objects.filter(status='COMPLETED').count(),
        'total_game_entries': GameParticipant.objects.count(),
        'total_deposits_approved': PaymentSubmission.objects.filter(status='APPROVED').count(),
        'total_products': Product.objects.count(),
    }
    return Response(data)
