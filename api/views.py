from rest_framework import viewsets, status
from rest_framework.decorators import action, api_view, permission_classes
from rest_framework.response import Response
from rest_framework.permissions import AllowAny, IsAuthenticated
from django.contrib.auth.models import User
from django.db import transaction
from django.utils import timezone
from decimal import Decimal

from .models import (
    UserProfile, SellerProfile, Product, Game, GameParticipant,
    GameResult, Wallet, WalletTransaction, PaymentSubmission,
    ProductDelivery, Notification, AuditLog
)
from .serializers import (
    UserSerializer, UserProfileSerializer, SellerProfileSerializer,
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


# --- Game Engine & Management Views ---
class GameViewSet(viewsets.ModelViewSet):
    queryset = Game.objects.all()
    serializer_class = GameSerializer

    @action(detail=True, methods=['post'])
    def approve_game(self, request, pk=None):
        game = self.get_object()
        game.status = 'APPROVED'
        game.save()
        AuditLog.objects.create(actor=request.user if request.user.is_authenticated else None, action="APPROVE_GAME", target_model="Game", details=f"Game {game.title} approved.")
        return Response({'message': 'Game approved by admin.'})

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
        if not user_id:
            return Response({'error': 'user_id is required'}, status=status.HTTP_400_BAD_REQUEST)

        try:
            user = User.objects.get(pk=user_id)
        except User.DoesNotExist:
            return Response({'error': 'User not found'}, status=status.HTTP_404_NOT_FOUND)

        if game.status != 'ACTIVE':
            return Response({'error': 'Game is not currently active.'}, status=status.HTTP_400_BAD_REQUEST)

        if game.participants.count() >= game.max_participants:
            return Response({'error': 'Game participant limit reached.'}, status=status.HTTP_400_BAD_REQUEST)

        wallet, _ = Wallet.objects.get_or_create(user=user)
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

            participant = GameParticipant.objects.create(
                game=game,
                user=user,
                selected_box=request.data.get('selected_box'),
                selected_number=request.data.get('selected_number'),
                selected_card=request.data.get('selected_card'),
                prediction_answer=request.data.get('prediction_answer'),
                timer_delta_ms=request.data.get('timer_delta_ms'),
            )

        return Response({'message': 'Joined game successfully!', 'participant_id': participant.id}, status=status.HTTP_201_CREATED)

    @action(detail=True, methods=['post'])
    def resolve_game(self, request, pk=None):
        game = self.get_object()
        
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

            result = GameResult.objects.create(
                game=game,
                winner=winner_participant.user,
                winning_value=str(winner_participant.selected_box or winner_participant.selected_number or winner_participant.selected_card or winner_participant.prediction_answer or winner_participant.timer_delta_ms),
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
