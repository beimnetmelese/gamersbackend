from django.test import TestCase
from django.contrib.auth.models import User
from decimal import Decimal
from .models import (
    SellerProfile, Product, Game, GameParticipant, GameResult, Wallet, UserProfile
)
from .engines import GameEngine

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
        self.assertIn("selected as winning treasure box", msg)

    def test_lowest_unique_number_engine(self):
        game = Game.objects.create(
            product=self.product,
            seller=self.seller,
            title="Lowest Unique Contest",
            game_type="LOWEST_UNIQUE",
            entry_fee=Decimal("50.00"),
            status="ACTIVE"
        )
        # u1 and u2 pick 5 (duplicate), u3 picks 7 (unique lowest)
        p1 = GameParticipant.objects.create(game=game, user=self.u1, selected_number=5)
        p2 = GameParticipant.objects.create(game=game, user=self.u2, selected_number=5)
        p3 = GameParticipant.objects.create(game=game, user=self.u3, selected_number=7)

        winner, msg = GameEngine.resolve_game(game)
        self.assertEqual(winner, p3)
        self.assertIn("Lowest unique number submitted was 7", msg)

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
        self.assertIn("Highest unique card was A", msg)

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
        p2 = GameParticipant.objects.create(game=game, user=self.u2, selected_number=41) # off by 1
        p3 = GameParticipant.objects.create(game=game, user=self.u3, selected_number=50)

        winner, msg = GameEngine.resolve_game(game)
        self.assertEqual(winner, p2)
        self.assertIn("off by 1", msg)
