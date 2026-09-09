from django.test import TestCase
from django.contrib.auth.models import User
from decimal import Decimal
from .models import (
    SellerProfile, Product, Game, GameParticipant, GameResult, Wallet, WalletTransaction,
    PaymentSubmission, WithdrawalRequest, Favorite, UserProfile
)
from .engines import GameEngine
from .services import (
    WalletService, PaymentService, WithdrawalService,
    NotificationService, AuthService
)

class GameEngineTests(TestCase):
    def setUp(self):
        self.seller_user = User.objects.create_user(username="seller1", password="password")
        UserProfile.objects.create(user=self.seller_user, role="SELLER")
        self.seller = SellerProfile.objects.create(
            user=self.seller_user,
            business_name="TechStore Ethiopia",
            phone_number="+251911111111",
            address="Bole, Addis Ababa"
        )
        self.product = Product.objects.create(
            seller=self.seller,
            title="iPhone 15 Pro",
            category="Electronics",
            description="Brand new sealed iPhone 15",
            estimated_value=Decimal("120000.00"),
            approval_status="APPROVED"
        )

        self.u1 = User.objects.create_user(username="user1", password="password")
        self.u2 = User.objects.create_user(username="user2", password="password")
        self.u3 = User.objects.create_user(username="user3", password="password")

    def test_treasure_box_engine(self):
        game = Game.objects.create(
            product=self.product,
            seller=self.seller,
            title="Treasure Box iPhone",
            game_type="TREASURE_BOX",
            entry_fee=Decimal("100.00"),
            status="ACTIVE"
        )
        p1 = GameParticipant.objects.create(game=game, user=self.u1, selected_box=12)
        p2 = GameParticipant.objects.create(game=game, user=self.u2, selected_box=45)

        winner, msg = GameEngine.resolve_game(game)
        self.assertIn(winner, [p1, p2])
        self.assertIn("Winning Box was", msg)

    def test_lowest_unique_number_engine(self):
        game = Game.objects.create(
            product=self.product,
            seller=self.seller,
            title="Lowest Unique Contest",
            game_type="LOWEST_UNIQUE",
            entry_fee=Decimal("50.00"),
            status="ACTIVE"
        )
        p1 = GameParticipant.objects.create(game=game, user=self.u1, selected_number=5)
        p2 = GameParticipant.objects.create(game=game, user=self.u2, selected_number=5)
        p3 = GameParticipant.objects.create(game=game, user=self.u3, selected_number=7)

        winner, msg = GameEngine.resolve_game(game)
        self.assertEqual(winner, p3)
        self.assertIn("Lowest Unique Number was 7", msg)

    def test_highest_card_engine(self):
        game = Game.objects.create(
            product=self.product,
            seller=self.seller,
            title="Highest Card Challenge",
            game_type="HIGHEST_CARD",
            entry_fee=Decimal("50.00"),
            status="ACTIVE"
        )
        p1 = GameParticipant.objects.create(game=game, user=self.u1, selected_card="10")
        p2 = GameParticipant.objects.create(game=game, user=self.u2, selected_card="K")
        p3 = GameParticipant.objects.create(game=game, user=self.u3, selected_card="A")

        winner, msg = GameEngine.resolve_game(game)
        self.assertEqual(winner, p3)
        self.assertIn("Highest Card was A", msg)

    def test_secret_number_engine(self):
        game = Game.objects.create(
            product=self.product,
            seller=self.seller,
            title="Secret Number 42",
            game_type="SECRET_NUMBER",
            entry_fee=Decimal("20.00"),
            secret_target="42",
            status="ACTIVE"
        )
        p1 = GameParticipant.objects.create(game=game, user=self.u1, selected_number=30)
        p2 = GameParticipant.objects.create(game=game, user=self.u2, selected_number=41)
        p3 = GameParticipant.objects.create(game=game, user=self.u3, selected_number=50)

        winner, msg = GameEngine.resolve_game(game)
        self.assertEqual(winner, p2)
        self.assertIn("off by 1", msg)


class FinancialAndServicesTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="testuser", password="password")
        self.admin = User.objects.create_user(username="adminuser", password="password", is_staff=True)
        UserProfile.objects.create(user=self.admin, role="ADMIN")
        self.wallet = WalletService.get_or_create_wallet(self.user)

    def test_deposit_and_idempotent_approval(self):
        success, msg, dep = PaymentService.create_deposit_submission(
            user=self.user,
            payment_method="Telebirr",
            transaction_id="TX_TEST_001",
            amount=Decimal("500.00")
        )
        self.assertTrue(success)
        self.assertEqual(dep.status, "PENDING")
        self.assertEqual(self.wallet.balance, Decimal("0.00"))

        # Approve once
        app_success, app_msg, dep = PaymentService.approve_deposit(dep.id, admin_user=self.admin)
        self.assertTrue(app_success)
        self.assertEqual(dep.status, "APPROVED")

        # Check wallet credited exactly 500 ETB
        self.wallet.refresh_from_db()
        self.assertEqual(self.wallet.balance, Decimal("500.00"))

        # Attempt double approval
        app_success2, app_msg2, dep = PaymentService.approve_deposit(dep.id, admin_user=self.admin)
        self.assertFalse(app_success2)
        self.assertIn("already been processed", app_msg2)

        # Verify wallet balance did NOT double-credit
        self.wallet.refresh_from_db()
        self.assertEqual(self.wallet.balance, Decimal("500.00"))

    def test_withdrawal_reservation_and_approval(self):
        # Give user 1000 ETB balance
        self.wallet.balance = Decimal("1000.00")
        self.wallet.save()

        # Request 400 ETB withdrawal
        success, msg, wd = WithdrawalService.create_withdrawal_request(
            user=self.user,
            withdrawal_method="Telebirr",
            account_number="0911223344",
            account_name="Test User",
            phone_number="0911223344",
            amount=Decimal("400.00")
        )
        self.assertTrue(success)
        self.wallet.refresh_from_db()

        # Balance remains 1000, reserved is 400, available is 600
        self.assertEqual(self.wallet.balance, Decimal("1000.00"))
        self.assertEqual(self.wallet.reserved_balance, Decimal("400.00"))
        self.assertEqual(self.wallet.available_balance, Decimal("600.00"))

        # Approve withdrawal
        app_success, app_msg, wd = WithdrawalService.approve_withdrawal(wd.id, admin_user=self.admin)
        self.assertTrue(app_success)
        self.wallet.refresh_from_db()

        # Balance becomes 600, reserved becomes 0
        self.assertEqual(self.wallet.balance, Decimal("600.00"))
        self.assertEqual(self.wallet.reserved_balance, Decimal("0.00"))
        self.assertEqual(self.wallet.available_balance, Decimal("600.00"))

    def test_withdrawal_rejection_releases_reserved_funds(self):
        self.wallet.balance = Decimal("1000.00")
        self.wallet.save()

        success, msg, wd = WithdrawalService.create_withdrawal_request(
            user=self.user,
            withdrawal_method="CBE Birr",
            account_number="1000123456",
            account_name="Test User",
            phone_number="0911223344",
            amount=Decimal("500.00")
        )
        self.assertTrue(success)
        self.wallet.refresh_from_db()
        self.assertEqual(self.wallet.available_balance, Decimal("500.00"))

        # Reject withdrawal
        rej_success, rej_msg, wd = WithdrawalService.reject_withdrawal(wd.id, admin_user=self.admin)
        self.assertTrue(rej_success)
        self.wallet.refresh_from_db()

        # Balance is still 1000, reserved is 0, available is 1000
        self.assertEqual(self.wallet.balance, Decimal("1000.00"))
        self.assertEqual(self.wallet.reserved_balance, Decimal("0.00"))
        self.assertEqual(self.wallet.available_balance, Decimal("1000.00"))

    def test_case_insensitive_authentication(self):
        u = User.objects.create_user(username="CaseUser", password="MySecretPassword123")
        
        auth1 = AuthService.authenticate_user("caseuser", "MySecretPassword123")
        self.assertIsNotNone(auth1)
        self.assertEqual(auth1, u)

        auth2 = AuthService.authenticate_user("CASEUSER", "MySecretPassword123")
        self.assertIsNotNone(auth2)
        self.assertEqual(auth2, u)

        auth3 = AuthService.authenticate_user("cAsEuSeR", "MySecretPassword123")
        self.assertIsNotNone(auth3)
        self.assertEqual(auth3, u)
        self.assertEqual(auth3, u)
