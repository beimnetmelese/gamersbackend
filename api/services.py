import os
import requests
import uuid
from decimal import Decimal
from typing import Tuple, Optional, Dict, Any
from django.db import transaction
from django.utils import timezone
from django.contrib.auth import authenticate
from django.contrib.auth.models import User
from .models import (
    Wallet, WalletTransaction, PaymentSubmission, WithdrawalRequest,
    UserProfile, Notification, AuditLog, Game, GameParticipant, GameResult
)

class NotificationService:
    @staticmethod
    def send_notification(user: User, title: str, message: str, event_type: str = 'SYSTEM') -> Notification:
        notif = Notification.objects.create(
            user=user,
            title=title,
            message=message,
            event_type=event_type
        )

        # Optional Telegram notification mirroring hook if configured
        try:
            token = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
            chat_id = getattr(getattr(user, 'profile', None), 'telegram_chat_id', None)
            if token and chat_id:
                text = f"<b>{title}</b>\n{message}"
                requests.post(
                    f"https://api.telegram.org/bot{token}/sendMessage",
                    json={"chat_id": chat_id, "text": text, "parse_mode": "HTML"},
                    timeout=2
                )
        except Exception:
            pass

        return notif



class WalletService:
    @staticmethod
    def get_or_create_wallet(user: User) -> Wallet:
        wallet, _ = Wallet.objects.get_or_create(
            user=user,
            defaults={'balance': Decimal('0.00'), 'reserved_balance': Decimal('0.00')}
        )
        return wallet

    @staticmethod
    def deduct_game_entry(user: User, game: Game, note: str = "") -> Tuple[bool, str, Optional[WalletTransaction]]:
        with transaction.atomic():
            wallet = WalletService.get_or_create_wallet(user)
            if wallet.available_balance < game.entry_fee:
                return False, f"Insufficient wallet balance. Available: {wallet.available_balance} ETB, Required: {game.entry_fee} ETB", None

            wallet.balance -= game.entry_fee
            wallet.save()

            tx = WalletTransaction.objects.create(
                wallet=wallet,
                transaction_type='GAME_ENTRY',
                direction='DEBIT',
                status='COMPLETED',
                amount=-game.entry_fee,
                reference_id=str(game.id),
                note=note or f"Joined game: {game.title}",
                related_game=game
            )

            NotificationService.send_notification(
                user=user,
                title="🎮 Game Entry Deduction",
                message=f"Deducted {game.entry_fee} ETB for joining '{game.title}'. Available balance: {wallet.available_balance} ETB.",
                event_type='GAME_EVENT'
            )

            return True, "Entry fee deducted successfully.", tx

    @staticmethod
    def credit_reward(user: User, amount: Decimal, reference_id: str = "", note: str = "") -> WalletTransaction:
        with transaction.atomic():
            wallet = WalletService.get_or_create_wallet(user)
            wallet.balance += amount
            wallet.save()

            tx = WalletTransaction.objects.create(
                wallet=wallet,
                transaction_type='REWARD',
                direction='CREDIT',
                status='COMPLETED',
                amount=amount,
                reference_id=reference_id,
                note=note or "Game prize reward"
            )

            NotificationService.send_notification(
                user=user,
                title="🏆 Prize Reward Credited",
                message=f"Received {amount} ETB reward! New balance: {wallet.balance} ETB.",
                event_type='GAME_WIN'
            )
            return tx


class PaymentService:
    @staticmethod
    def create_deposit_submission(
        user: User,
        payment_method: str,
        transaction_id: str,
        amount: Decimal,
        proof_image=None
    ) -> Tuple[bool, str, Optional[PaymentSubmission]]:
        transaction_id = str(transaction_id).strip()
        if PaymentSubmission.objects.filter(transaction_id__iexact=transaction_id).exists():
            return False, f"Transaction ID '{transaction_id}' has already been submitted.", None

        if amount <= Decimal('0.00'):
            return False, "Deposit amount must be greater than 0.", None

        deposit = PaymentSubmission.objects.create(
            user=user,
            payment_method=payment_method,
            transaction_id=transaction_id,
            amount=amount,
            proof_image=proof_image,
            status='PENDING'
        )

        NotificationService.send_notification(
            user=user,
            title="💳 Deposit Submitted",
            message=f"Deposit proof of {amount} ETB (Tx: {transaction_id}) submitted. Waiting for admin verification.",
            event_type='DEPOSIT'
        )

        return True, "Deposit submission recorded successfully. Pending Admin verification.", deposit

    @staticmethod
    def approve_deposit(payment_id: int, admin_user: Optional[User] = None, admin_note: str = "Approved by Admin") -> Tuple[bool, str, Optional[PaymentSubmission]]:
        with transaction.atomic():
            try:
                payment = PaymentSubmission.objects.select_for_update().get(pk=payment_id)
            except PaymentSubmission.DoesNotExist:
                return False, "Deposit submission not found.", None

            # Strict Idempotency Check
            if payment.status != 'PENDING':
                return False, f"Deposit has already been processed with status: {payment.status}", payment

            payment.status = 'APPROVED'
            payment.reviewed_at = timezone.now()
            payment.admin_note = admin_note
            payment.save()

            wallet = WalletService.get_or_create_wallet(payment.user)
            wallet.balance += payment.amount
            wallet.save()

            WalletTransaction.objects.create(
                wallet=wallet,
                transaction_type='DEPOSIT',
                direction='CREDIT',
                status='COMPLETED',
                amount=payment.amount,
                reference_id=payment.transaction_id,
                note=f"Approved Deposit via {payment.payment_method}",
                related_deposit=payment
            )

            NotificationService.send_notification(
                user=payment.user,
                title="✅ Deposit Approved!",
                message=f"Your deposit of {payment.amount} ETB (Tx: {payment.transaction_id}) has been approved! Wallet balance updated.",
                event_type='DEPOSIT'
            )

            AuditLog.objects.create(
                actor=admin_user,
                action="APPROVE_DEPOSIT",
                target_model="PaymentSubmission",
                details=f"Approved deposit #{payment.id} (Tx: {payment.transaction_id}, Amount: {payment.amount} ETB) for user {payment.user.username}"
            )

            return True, f"Deposit of {payment.amount} ETB approved and credited to {payment.user.username}.", payment

    @staticmethod
    def reject_deposit(payment_id: int, admin_user: Optional[User] = None, admin_note: str = "Rejected by Admin") -> Tuple[bool, str, Optional[PaymentSubmission]]:
        with transaction.atomic():
            try:
                payment = PaymentSubmission.objects.select_for_update().get(pk=payment_id)
            except PaymentSubmission.DoesNotExist:
                return False, "Deposit submission not found.", None

            if payment.status != 'PENDING':
                return False, f"Deposit has already been processed with status: {payment.status}", payment

            payment.status = 'REJECTED'
            payment.reviewed_at = timezone.now()
            payment.admin_note = admin_note
            payment.save()

            NotificationService.send_notification(
                user=payment.user,
                title="❌ Deposit Rejected",
                message=f"Your deposit request of {payment.amount} ETB (Tx: {payment.transaction_id}) was rejected. Reason: {admin_note}",
                event_type='DEPOSIT'
            )

            AuditLog.objects.create(
                actor=admin_user,
                action="REJECT_DEPOSIT",
                target_model="PaymentSubmission",
                details=f"Rejected deposit #{payment.id} for user {payment.user.username}"
            )

            return True, "Deposit submission rejected.", payment


class WithdrawalService:
    @staticmethod
    def create_withdrawal_request(
        user: User,
        withdrawal_method: str,
        account_number: str,
        account_name: str,
        phone_number: str,
        amount: Decimal
    ) -> Tuple[bool, str, Optional[WithdrawalRequest]]:
        if amount < Decimal('100.00'):
            return False, "Minimum withdrawal amount is 100 ETB.", None

        with transaction.atomic():
            wallet = WalletService.get_or_create_wallet(user)

            if wallet.available_balance < amount:
                return False, f"Insufficient available balance. Available: {wallet.available_balance} ETB, Requested: {amount} ETB", None

            # Reserve funds
            wallet.reserved_balance += amount
            wallet.save()

            tx_ref = f"WD-{uuid.uuid4().hex[:10].upper()}"

            withdrawal = WithdrawalRequest.objects.create(
                user=user,
                wallet=wallet,
                withdrawal_method=withdrawal_method,
                account_number=account_number,
                account_name=account_name,
                phone_number=phone_number,
                amount=amount,
                transaction_id=tx_ref,
                status='PENDING'
            )

            WalletTransaction.objects.create(
                wallet=wallet,
                transaction_type='WITHDRAWAL',
                direction='DEBIT',
                status='PENDING',
                amount=-amount,
                reference_id=tx_ref,
                note=f"Pending Withdrawal to {withdrawal_method} ({account_number})",
                related_withdrawal=withdrawal
            )

            NotificationService.send_notification(
                user=user,
                title="💸 Withdrawal Requested",
                message=f"Withdrawal request of {amount} ETB to {withdrawal_method} ({account_number}) submitted. Funds reserved.",
                event_type='WITHDRAWAL'
            )

            return True, f"Withdrawal request of {amount} ETB submitted successfully. Waiting for admin processing.", withdrawal

    @staticmethod
    def approve_withdrawal(withdrawal_id: int, admin_user: Optional[User] = None, admin_note: str = "Approved by Admin") -> Tuple[bool, str, Optional[WithdrawalRequest]]:
        with transaction.atomic():
            try:
                withdrawal = WithdrawalRequest.objects.select_for_update().get(pk=withdrawal_id)
            except WithdrawalRequest.DoesNotExist:
                return False, "Withdrawal request not found.", None

            # Strict Idempotency Check
            if withdrawal.status != 'PENDING':
                return False, f"Withdrawal request has already been processed with status: {withdrawal.status}", withdrawal

            wallet = withdrawal.wallet
            amount = withdrawal.amount

            withdrawal.status = 'APPROVED'
            withdrawal.reviewed_at = timezone.now()
            withdrawal.admin_note = admin_note
            withdrawal.save()

            # Deduct balance and clear reserved balance
            wallet.balance -= amount
            wallet.reserved_balance = max(Decimal('0.00'), wallet.reserved_balance - amount)
            wallet.save()

            # Update corresponding pending transaction
            WalletTransaction.objects.filter(related_withdrawal=withdrawal).update(status='COMPLETED')

            NotificationService.send_notification(
                user=withdrawal.user,
                title="✅ Withdrawal Approved!",
                message=f"Your withdrawal of {amount} ETB (Ref: {withdrawal.transaction_id}) to {withdrawal.withdrawal_method} has been processed!",
                event_type='WITHDRAWAL'
            )

            AuditLog.objects.create(
                actor=admin_user,
                action="APPROVE_WITHDRAWAL",
                target_model="WithdrawalRequest",
                details=f"Approved withdrawal #{withdrawal.id} ({amount} ETB) for user {withdrawal.user.username}"
            )

            return True, f"Withdrawal of {amount} ETB approved and completed for {withdrawal.user.username}.", withdrawal

    @staticmethod
    def reject_withdrawal(withdrawal_id: int, admin_user: Optional[User] = None, admin_note: str = "Rejected by Admin") -> Tuple[bool, str, Optional[WithdrawalRequest]]:
        with transaction.atomic():
            try:
                withdrawal = WithdrawalRequest.objects.select_for_update().get(pk=withdrawal_id)
            except WithdrawalRequest.DoesNotExist:
                return False, "Withdrawal request not found.", None

            if withdrawal.status != 'PENDING':
                return False, f"Withdrawal request has already been processed with status: {withdrawal.status}", withdrawal

            wallet = withdrawal.wallet
            amount = withdrawal.amount

            withdrawal.status = 'REJECTED'
            withdrawal.reviewed_at = timezone.now()
            withdrawal.admin_note = admin_note
            withdrawal.save()

            # Release reserved balance
            wallet.reserved_balance = max(Decimal('0.00'), wallet.reserved_balance - amount)
            wallet.save()

            WalletTransaction.objects.filter(related_withdrawal=withdrawal).update(status='REJECTED')

            NotificationService.send_notification(
                user=withdrawal.user,
                title="❌ Withdrawal Rejected",
                message=f"Your withdrawal request of {amount} ETB (Ref: {withdrawal.transaction_id}) was rejected. Reserved funds released back to your available balance. Reason: {admin_note}",
                event_type='WITHDRAWAL'
            )

            AuditLog.objects.create(
                actor=admin_user,
                action="REJECT_WITHDRAWAL",
                target_model="WithdrawalRequest",
                details=f"Rejected withdrawal #{withdrawal.id} for user {withdrawal.user.username}"
            )

            return True, "Withdrawal request rejected. Reserved funds released.", withdrawal


class AuthService:
    @staticmethod
    def authenticate_user(username_or_email: str, password: str) -> Optional[User]:
        username_or_email = username_or_email.strip()
        # Find user case-insensitively by username or email
        user_obj = User.objects.filter(username__iexact=username_or_email).first()
        if not user_obj:
            user_obj = User.objects.filter(email__iexact=username_or_email).first()

        if user_obj and user_obj.check_password(password):
            return user_obj
        return None

    @staticmethod
    def get_user_stats(user: User) -> Dict[str, Any]:
        entries = GameParticipant.objects.filter(user=user)
        total_entries = entries.count()
        games_played = entries.values('game').distinct().count()
        
        wins = GameResult.objects.filter(winner=user).count()
        losses = max(0, games_played - wins)
        win_rate = round((wins / games_played * 100), 1) if games_played > 0 else 0.0

        total_spent = sum([p.game.entry_fee for p in entries.select_related('game')])

        return {
            'games_played': games_played,
            'games_won': wins,
            'games_lost': losses,
            'win_rate': win_rate,
            'total_entries': total_entries,
            'total_spent': float(total_spent)
        }
