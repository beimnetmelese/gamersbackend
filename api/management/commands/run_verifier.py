import time
import logging
from decimal import Decimal
from django.core.management.base import BaseCommand
from django.utils import timezone
from api.models import PaymentSubmission
from api.verification_service import PaymentVerificationService
from api.services import WalletService, WalletTransaction, NotificationService

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = "Independent Payment Verification Background Worker. Continuously processes PENDING deposits."

    def add_arguments(self, parser):
        parser.add_argument(
            '--once',
            action='store_true',
            help='Run verification worker once for pending deposits and exit.',
        )
        parser.add_argument(
            '--interval',
            type=int,
            default=10,
            help='Polling interval in seconds when running as daemon (default: 10).',
        )

    def handle(self, *args, **options):
        run_once = options['once']
        interval = options['interval']

        self.stdout.write(self.style.SUCCESS("=================================================="))
        self.stdout.write(self.style.SUCCESS("[+] Payment Verification Background Worker Started"))
        self.stdout.write(self.style.SUCCESS(f"Mode: {'Single Pass' if run_once else f'Daemon (Polling every {interval}s)'}"))
        self.stdout.write(self.style.SUCCESS("=================================================="))

        verifier = PaymentVerificationService()

        MAX_RETRIES = 3
        RETRY_DELAY_SECONDS = 120  # Minimum 2 minutes between retries for the same deposit

        while True:
            try:
                pending_deposits = PaymentSubmission.objects.filter(status='PENDING').order_by('submitted_at')
                count = pending_deposits.count()

                if count > 0:
                    self.stdout.write(f"\n[+] Found {count} pending deposit(s) to verify...")
                    for deposit in pending_deposits:
                        prior_logs = deposit.verification_logs.order_by('-created_at')
                        attempt_count = prior_logs.count()

                        # 1. Check Max Attempts Threshold
                        if attempt_count >= MAX_RETRIES:
                            deposit.status = 'REJECTED'
                            deposit.reviewed_at = timezone.now()
                            deposit.admin_note = f"Max verification retries ({MAX_RETRIES}/{MAX_RETRIES}) exceeded due to persistent scraper/system error. Flagged for Manual Admin Review."
                            deposit.save()

                            NotificationService.send_notification(
                                user=deposit.user,
                                title="Verification Pending Admin Review",
                                message=f"Deposit Tx {deposit.transaction_id} could not be automatically verified after {MAX_RETRIES} attempts. An admin will review your deposit manually.",
                                event_type='DEPOSIT'
                            )

                            self.stdout.write(self.style.ERROR(
                                f"    [X] MAX RETRIES EXCEEDED ({attempt_count}/{MAX_RETRIES}): Deposit #{deposit.id} (Tx: {deposit.transaction_id}) marked REJECTED for Manual Admin Review."
                            ))
                            continue

                        # 2. Check Retry Throttle Delay (Minimum 120s between retries)
                        if attempt_count > 0:
                            last_log = prior_logs.first()
                            if last_log and last_log.created_at:
                                elapsed_sec = (timezone.now() - last_log.created_at).total_seconds()
                                if elapsed_sec < RETRY_DELAY_SECONDS:
                                    wait_time = int(RETRY_DELAY_SECONDS - elapsed_sec)
                                    self.stdout.write(self.style.WARNING(
                                        f"    [!] THROTTLED: Deposit #{deposit.id} checked {int(elapsed_sec)}s ago (Attempt {attempt_count}/{MAX_RETRIES}). Waiting {wait_time}s before next retry."
                                    ))
                                    continue

                        self.stdout.write(f" -> Verifying Deposit #{deposit.id} (Attempt {attempt_count + 1}/{MAX_RETRIES}) | Tx: {deposit.transaction_id} | Bank: {deposit.bank.upper()} | Amount: {deposit.amount} ETB | User: {deposit.user.username}")

                        result_data, http_status = verifier.verify_payment(
                            reference_id=deposit.transaction_id,
                            requested_amount=deposit.amount,
                            bank=deposit.bank,
                            user=deposit.user,
                            submission=deposit
                        )

                        if result_data.get("verified") is True:
                            deposit.status = 'APPROVED'
                            deposit.reviewed_at = timezone.now()
                            deposit.admin_note = "Auto-Verified via Background Worker"
                            deposit.save()

                            wallet = WalletService.get_or_create_wallet(deposit.user)
                            wallet.balance += deposit.amount
                            wallet.save()

                            WalletTransaction.objects.create(
                                wallet=wallet,
                                transaction_type='DEPOSIT',
                                direction='CREDIT',
                                status='COMPLETED',
                                amount=deposit.amount,
                                reference_id=deposit.transaction_id,
                                note=f"Auto-Verified Deposit via Background Worker ({deposit.bank.upper()})",
                                related_deposit=deposit
                            )

                            NotificationService.send_notification(
                                user=deposit.user,
                                title="Deposit Approved!",
                                message=f"Your deposit of {deposit.amount} ETB (Tx: {deposit.transaction_id}) was verified by the background worker and credited to your wallet balance.",
                                event_type='DEPOSIT'
                            )

                            self.stdout.write(self.style.SUCCESS(f"    [V] APPROVED & CREDITED: {deposit.amount} ETB to {deposit.user.username}"))

                        elif result_data.get("verified") is False and (result_data.get("success") is True or http_status in (400, 404)):
                            deposit.status = 'REJECTED'
                            deposit.reviewed_at = timezone.now()
                            raw_msg = result_data.get("message", "Verification Failed")
                            if http_status == 404 or "not found" in raw_msg.lower():
                                raw_msg = "Invalid payment/transaction ID for selected bank."
                            deposit.admin_note = f"Rejected: {raw_msg}"
                            deposit.save()

                            NotificationService.send_notification(
                                user=deposit.user,
                                title="Verification Failed",
                                message=f"Deposit verification for Tx {deposit.transaction_id} rejected: {raw_msg}",
                                event_type='DEPOSIT'
                            )

                            self.stdout.write(self.style.ERROR(f"    [X] REJECTED: {raw_msg}"))

                        else:
                            self.stdout.write(self.style.WARNING(f"    [!] PENDING RETRY (Attempt {attempt_count + 1}/{MAX_RETRIES}): Service response status {http_status} ({result_data.get('message')})"))

            except Exception as e:
                self.stdout.write(self.style.ERROR(f"Error in verification loop: {e}"))

            if run_once:
                self.stdout.write(self.style.SUCCESS("\n[+] Worker completed single pass execution."))
                break

            time.sleep(interval)
