import logging
from decimal import Decimal
from typing import Optional, Tuple, Dict, Any
from django.contrib.auth.models import User
from django.utils import timezone
from .models import PaymentVerificationLog, PaymentSubmission, AuditLog
from .scraper import (
    get_scraper_for_bank,
    CBEReceiptNotFoundException,
    CBEScraperFetchException,
    CBEParseException
)

logger = logging.getLogger(__name__)

EXPECTED_RECEIVER_NAME = "Beimnet Melese Kebede"


def is_receiver_verified(actual_receiver: str, expected_receiver: str = EXPECTED_RECEIVER_NAME) -> bool:
    """
    Check if the receipt's receiver / credited party name matches expected receiver name.
    Ignores case, normalizes whitespace, and supports token-based matching.
    """
    if not actual_receiver:
        return False
    clean_actual = ' '.join(str(actual_receiver).split()).lower()
    clean_expected = ' '.join(str(expected_receiver).split()).lower()

    if clean_actual == clean_expected:
        return True

    expected_words = [w for w in clean_expected.split() if len(w) > 1]
    if expected_words and all(word in clean_actual for word in expected_words):
        return True

    return False


def notify_admins(title: str, message: str, event_type: str = 'DEPOSIT'):
    """Utility to alert all Admin users with in-app notification & audit log (deduplicated)."""
    from .services import NotificationService
    from .models import Notification
    admin_users = User.objects.filter(is_staff=True) | User.objects.filter(profile__role='ADMIN')
    admin_users = admin_users.distinct()

    for admin in admin_users:
        # Rate limit/Deduplicate: Skip if an identical notification was sent to this admin within the last 1 hour
        recent_cutoff = timezone.now() - timezone.timedelta(hours=1)
        if Notification.objects.filter(
            user=admin,
            event_type=event_type,
            title=title,
            message=message,
            created_at__gte=recent_cutoff
        ).exists():
            logger.info(f"Skipping duplicate admin notification '{title}' to user {admin.username}")
            continue

        try:
            NotificationService.send_notification(
                user=admin,
                title=title,
                message=message,
                event_type=event_type
            )
        except Exception as e:
            logger.warning(f"Failed to send admin notification to {admin.username}: {e}")


class PaymentVerificationService:
    """
    Core Payment Verification Service for CBE and Telebirr receipts.
    Evaluates reference, amount, and receiver verification, records audit logs,
    and sends admin notifications on completion.
    """

    def __init__(self, scraper=None):
        self._custom_scraper = scraper

    def get_scraper(self, bank: str):
        if self._custom_scraper:
            return self._custom_scraper
        return get_scraper_for_bank(bank)

    def verify_payment(
        self,
        reference_id: str,
        requested_amount: Decimal,
        bank: str = 'cbe',
        user: Optional[User] = None,
        submission: Optional[PaymentSubmission] = None
    ) -> Tuple[Dict[str, Any], int]:
        """
        Verify payment reference ID against CBE or Telebirr receipt scrapers.
        Returns a tuple of (response_dict, http_status_code).
        """
        bank_clean = (bank or 'cbe').lower().strip()
        requested_amount_dec = Decimal(str(requested_amount)).quantize(Decimal('0.01'))
        requested_amount_str = f"{requested_amount_dec:.2f}"

        # 1. Check if this receipt reference has ALREADY been successfully verified for THIS bank
        existing_successful = PaymentVerificationLog.objects.filter(
            bank=bank_clean,
            reference_id=reference_id,
            is_verified=True
        ).first()

        if existing_successful:
            logger.info(f"Duplicate verification attempt for bank '{bank_clean}' and reference_id '{reference_id}'.")
            verified_amt_str = (
                f"{existing_successful.verified_amount:.2f}"
                if existing_successful.verified_amount is not None
                else None
            )

            amount_verified = False
            if existing_successful.verified_amount is not None:
                amount_verified = (existing_successful.verified_amount == requested_amount_dec)

            verified_receiver = None
            if existing_successful.receipt_data:
                tx_data = existing_successful.receipt_data.get("transaction", {})
                cust_data = existing_successful.receipt_data.get("customer", {})
                verified_receiver = tx_data.get("receiver") or tx_data.get("credited_party_name") or cust_data.get("customer_name")

            receiver_verified = existing_successful.receiver_verified or is_receiver_verified(verified_receiver)

            # Record Duplicate Log
            PaymentVerificationLog.objects.create(
                user=user,
                submission=submission,
                bank=bank_clean,
                reference_id=reference_id,
                requested_amount=requested_amount_dec,
                verified_amount=existing_successful.verified_amount,
                currency=existing_successful.currency or "ETB",
                reference_verified=True,
                amount_verified=amount_verified,
                receiver_verified=receiver_verified,
                is_verified=False,
                status="DUPLICATE",
                error_message="This receipt reference has already been used and verified.",
                receipt_data=existing_successful.receipt_data
            )

            msg = f"This {bank_clean.upper()} receipt reference has already been used and verified."
            
            notify_admins(
                title="⚠️ Duplicate Payment Attempt",
                message=f"User '{user.username if user else 'Anonymous'}' attempted duplicate verification for Tx '{reference_id}' ({bank_clean.upper()}).",
                event_type='DEPOSIT'
            )

            return {
                "success": True,
                "verified": False,
                "already_used": True,
                "bank": bank_clean,
                "reference_id": reference_id,
                "reference_verified": True,
                "amount_verified": amount_verified,
                "receiver_verified": receiver_verified,
                "expected_receiver": EXPECTED_RECEIVER_NAME,
                "verified_receiver": verified_receiver,
                "requested_amount": requested_amount_str,
                "verified_amount": verified_amt_str,
                "currency": existing_successful.currency or "ETB",
                "message": msg,
                "receipt": existing_successful.receipt_data
            }, 200

        # 2. Fetch receipt from bank scraper
        scraper = self.get_scraper(bank_clean)
        try:
            receipt_data = scraper.scrape(reference_id)
        except CBEReceiptNotFoundException as err:
            logger.info(f"{bank_clean.upper()} receipt not found for reference_id '{reference_id}': {err}")
            PaymentVerificationLog.objects.create(
                user=user,
                submission=submission,
                bank=bank_clean,
                reference_id=reference_id,
                requested_amount=requested_amount_dec,
                is_verified=False,
                status="NOT_FOUND",
                error_message=str(err)
            )
            notify_admins(
                title="❌ Verification Failed (Receipt Not Found)",
                message=f"Verification failed for User '{user.username if user else 'N/A'}' (Tx: {reference_id}, Bank: {bank_clean.upper()}). Receipt not found.",
                event_type='DEPOSIT'
            )
            return {
                "success": False,
                "verified": False,
                "already_used": False,
                "bank": bank_clean,
                "reference_id": reference_id,
                "reference_verified": False,
                "amount_verified": False,
                "receiver_verified": False,
                "expected_receiver": EXPECTED_RECEIVER_NAME,
                "verified_receiver": None,
                "message": f"Unable to find or retrieve the {bank_clean.upper()} receipt."
            }, 404
        except (CBEScraperFetchException, CBEParseException) as err:
            logger.error(f"{bank_clean.upper()} scraper fetch/parse error for reference_id '{reference_id}': {err}")
            PaymentVerificationLog.objects.create(
                user=user,
                submission=submission,
                bank=bank_clean,
                reference_id=reference_id,
                requested_amount=requested_amount_dec,
                is_verified=False,
                status="SERVICE_ERROR",
                error_message=str(err)
            )
            notify_admins(
                title="⚠️ Verifier Service Exception",
                message=f"Bank service fetch error during verification of Tx '{reference_id}' ({bank_clean.upper()}): {err}",
                event_type='SYSTEM'
            )
            return {
                "success": False,
                "verified": False,
                "already_used": False,
                "bank": bank_clean,
                "reference_id": reference_id,
                "reference_verified": False,
                "amount_verified": False,
                "receiver_verified": False,
                "expected_receiver": EXPECTED_RECEIVER_NAME,
                "verified_receiver": None,
                "message": f"{bank_clean.upper()} receipt service is temporarily unavailable or unreachable."
            }, 502
        except Exception as err:
            logger.error(f"Unexpected error during verification for reference_id '{reference_id}': {err}")
            PaymentVerificationLog.objects.create(
                user=user,
                submission=submission,
                bank=bank_clean,
                reference_id=reference_id,
                requested_amount=requested_amount_dec,
                is_verified=False,
                status="SYSTEM_ERROR",
                error_message=str(err)
            )
            return {
                "success": False,
                "verified": False,
                "already_used": False,
                "bank": bank_clean,
                "reference_id": reference_id,
                "reference_verified": False,
                "amount_verified": False,
                "receiver_verified": False,
                "expected_receiver": EXPECTED_RECEIVER_NAME,
                "verified_receiver": None,
                "message": "An error occurred while verifying the payment."
            }, 500

        # Parse extracted details
        transaction_info = receipt_data.get("transaction", {})
        transferred_amount_str = transaction_info.get("transferred_amount")
        currency = transaction_info.get("currency") or "ETB"

        verified_amount_str = None
        amount_verified = False

        if transferred_amount_str is not None:
            try:
                transferred_amount_dec = Decimal(str(transferred_amount_str)).quantize(Decimal('0.01'))
                verified_amount_str = f"{transferred_amount_dec:.2f}"
                amount_verified = (transferred_amount_dec == requested_amount_dec)
            except Exception as e:
                logger.warning(f"Error parsing transferred amount Decimal: {e}")
                amount_verified = False
        else:
            amount_verified = False

        actual_receiver_name = transaction_info.get("receiver")
        receiver_verified = is_receiver_verified(actual_receiver_name)

        has_transaction_details = (
            transferred_amount_str is not None or
            transaction_info.get("payer") is not None or
            transaction_info.get("reference_no") is not None
        )
        reference_verified = has_transaction_details
        is_verified = reference_verified and amount_verified and receiver_verified

        # Save verification log record
        v_log = PaymentVerificationLog.objects.create(
            user=user,
            submission=submission,
            bank=bank_clean,
            reference_id=reference_id,
            requested_amount=requested_amount_dec,
            verified_amount=Decimal(verified_amount_str) if verified_amount_str else None,
            currency=currency,
            reference_verified=reference_verified,
            amount_verified=amount_verified,
            receiver_verified=receiver_verified,
            is_verified=is_verified,
            status=receipt_data.get("status") or ("SUCCEEDED" if is_verified else "FAILED"),
            receipt_data=receipt_data
        )

        if is_verified:
            notify_admins(
                title="✅ Payment Verification SUCCEEDED",
                message=f"Verified deposit of {requested_amount_str} ETB for User '{user.username if user else 'N/A'}' (Tx: {reference_id}, Bank: {bank_clean.upper()}).",
                event_type='DEPOSIT'
            )
            AuditLog.objects.create(
                actor=user,
                action="VERIFY_PAYMENT_SUCCESS",
                target_model="PaymentSubmission",
                details=f"Payment verification succeeded for Tx {reference_id} ({requested_amount_str} ETB, Bank: {bank_clean})"
            )
            return {
                "success": True,
                "verified": True,
                "already_used": False,
                "bank": bank_clean,
                "reference_id": reference_id,
                "reference_verified": True,
                "amount_verified": True,
                "receiver_verified": True,
                "expected_receiver": EXPECTED_RECEIVER_NAME,
                "verified_receiver": actual_receiver_name,
                "requested_amount": requested_amount_str,
                "verified_amount": verified_amount_str,
                "currency": currency,
                "receipt": receipt_data
            }, 200

        # Handle Mismatch cases
        mismatches = []
        if not reference_verified:
            mismatches.append("reference verification failed")
        if not amount_verified:
            mismatches.append("payment amount does not match the receipt")
        if not receiver_verified:
            recipient_label = "Credited Party name" if bank_clean == 'telebirr' else "Receiver name"
            mismatches.append(f"{recipient_label} does not match expected ('{EXPECTED_RECEIVER_NAME}')")

        message = "; ".join(mismatches).capitalize() + "."

        notify_admins(
            title="⚠️ Payment Verification FAILED (Mismatch)",
            message=f"Verification mismatch for User '{user.username if user else 'N/A'}' (Tx: {reference_id}, Bank: {bank_clean.upper()}): {message}",
            event_type='DEPOSIT'
        )

        AuditLog.objects.create(
            actor=user,
            action="VERIFY_PAYMENT_MISMATCH",
            target_model="PaymentSubmission",
            details=f"Payment verification mismatch for Tx {reference_id} ({requested_amount_str} ETB): {message}"
        )

        return {
            "success": True,
            "verified": False,
            "already_used": False,
            "bank": bank_clean,
            "reference_id": reference_id,
            "reference_verified": reference_verified,
            "amount_verified": amount_verified,
            "receiver_verified": receiver_verified,
            "expected_receiver": EXPECTED_RECEIVER_NAME,
            "verified_receiver": actual_receiver_name,
            "requested_amount": requested_amount_str,
            "verified_amount": verified_amount_str,
            "currency": currency,
            "message": message,
            "receipt": receipt_data
        }, 200
