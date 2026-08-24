from django.core.management.base import BaseCommand
from django.contrib.auth.models import User
from decimal import Decimal
from api.models import Category, UserProfile, SellerProfile, Product, Game, GameParticipant, Wallet, WalletTransaction

class Command(BaseCommand):
    help = "Seed initial categories, sellers, products, and ALL 8 games for Developer 2"

    def handle(self, *args, **options):
        # Create seller user
        user, _ = User.objects.get_or_create(username='seller_tech', defaults={'email': 'tech@addis.et'})
        user.set_password('pass123')
        user.save()
        UserProfile.objects.get_or_create(user=user, defaults={'role': 'SELLER'})
        Wallet.objects.get_or_create(user=user, defaults={'balance': Decimal('50000.00')})

        seller, _ = SellerProfile.objects.get_or_create(
            user=user,
            defaults={
                'business_name': 'Addis Tech Hub',
                'description': 'Leading electronics and gaming supplier in Addis Ababa',
                'phone_number': '+251911223344',
                'address': 'Bole Medhanialem, Addis Ababa',
                'status': 'VERIFIED'
            }
        )

        categories = [
            ('Phones', 'phones', 'smartphone', 'Smartphones and mobile devices'),
            ('Laptops', 'laptops', 'laptop', 'MacBooks and laptops'),
            ('Gaming', 'gaming', 'gamepad-2', 'Consoles, controllers, and VR'),
            ('Electronics', 'electronics', 'tv', 'Smart TVs, cameras, and audio'),
            ('Fashion', 'fashion', 'shirt', 'Luxury watches and apparel'),
            ('Home', 'home', 'home', 'Smart home devices'),
            ('Vehicles', 'vehicles', 'car', 'Electric scooters and bikes'),
            ('Other', 'other', 'package', 'Gift cards and novelty items'),
        ]

        for name, slug, icon, desc in categories:
            Category.objects.get_or_create(name=name, defaults={'slug': slug, 'icon': icon, 'description': desc})

        # Products
        p1, _ = Product.objects.get_or_create(
            title="PlayStation 5 Digital Edition (Slim)",
            defaults={
                'seller': seller, 'category': 'Gaming',
                'description': 'Brand new 1TB PS5 Digital Edition console with extra DualSense controller.',
                'image_url': 'https://images.unsplash.com/photo-1606813907291-d86efa9b94db?auto=format&fit=crop&w=600&q=80',
                'condition': 'NEW', 'estimated_value': Decimal('65000.00'), 'location': 'Bole, Addis Ababa', 'approval_status': 'APPROVED'
            }
        )

        p2, _ = Product.objects.get_or_create(
            title="iPhone 15 Pro Max - 256GB Natural Titanium",
            defaults={
                'seller': seller, 'category': 'Phones',
                'description': 'Unopened sealed box iPhone 15 Pro Max with official warranty.',
                'image_url': 'https://images.unsplash.com/photo-1695048133142-1a20484d2569?auto=format&fit=crop&w=600&q=80',
                'condition': 'NEW', 'estimated_value': Decimal('145000.00'), 'location': 'Kazanchis, Addis Ababa', 'approval_status': 'APPROVED'
            }
        )

        p3, _ = Product.objects.get_or_create(
            title="Apple MacBook Pro 14 M3 Chip",
            defaults={
                'seller': seller, 'category': 'Laptops',
                'description': 'M3 chip, 16GB Unified Memory, 512GB SSD Space Gray.',
                'image_url': 'https://images.unsplash.com/photo-1517336714731-489689fd1ca8?auto=format&fit=crop&w=600&q=80',
                'condition': 'NEW', 'estimated_value': Decimal('185000.00'), 'location': 'Piassa, Addis Ababa', 'approval_status': 'APPROVED'
            }
        )

        p4, _ = Product.objects.get_or_create(
            title="Sony WH-1000XM5 Wireless Headphones",
            defaults={
                'seller': seller, 'category': 'Electronics',
                'description': 'Industry-leading noise canceling wireless over-ear headphones.',
                'image_url': 'https://images.unsplash.com/photo-1505740420928-5e560c06d30e?auto=format&fit=crop&w=600&q=80',
                'condition': 'NEW', 'estimated_value': Decimal('28000.00'), 'location': 'Sarbet, Addis Ababa', 'approval_status': 'APPROVED'
            }
        )

        p5, _ = Product.objects.get_or_create(
            title="Rolex Submariner Date Luxury Watch",
            defaults={
                'seller': seller, 'category': 'Fashion',
                'description': 'Oystersteel luxury timepiece with black dial and ceramic bezel.',
                'image_url': 'https://images.unsplash.com/photo-1523275335684-37898b6baf30?auto=format&fit=crop&w=600&q=80',
                'condition': 'NEW', 'estimated_value': Decimal('650000.00'), 'location': 'Bole Medhanialem, Addis Ababa', 'approval_status': 'APPROVED'
            }
        )

        p6, _ = Product.objects.get_or_create(
            title="Segway Ninebot Max G30 Electric Scooter",
            defaults={
                'seller': seller, 'category': 'Vehicles',
                'description': '65km long range electric commuter scooter with dual brakes.',
                'image_url': 'https://images.unsplash.com/photo-1588854337221-4cf9fa96059c?auto=format&fit=crop&w=600&q=80',
                'condition': 'NEW', 'estimated_value': Decimal('58000.00'), 'location': 'CMC, Addis Ababa', 'approval_status': 'APPROVED'
            }
        )

        # Clear old HEAD_TO_HEAD, TOURNAMENT, and PREDICTION games if present
        Game.objects.filter(game_type__in=['HEAD_TO_HEAD', 'TOURNAMENT', 'PREDICTION']).delete()

        # 5 Core Game Types
        games_list = [
            {
                'title': 'PS5 Slim - Treasure Box Challenge', 'product': p1, 'game_type': 'TREASURE_BOX',
                'entry_fee': Decimal('500.00'), 'max_participants': 100, 'total_boxes': 100, 'duration_minutes': 180,
                'rules_description': 'Choose 1 box out of 100. Winning box is selected randomly from chosen boxes.',
                'is_featured': True, 'is_recommended': True, 'views_count': 1450
            },
            {
                'title': 'iPhone 15 Pro - Lowest Unique Number', 'product': p2, 'game_type': 'LOWEST_UNIQUE',
                'entry_fee': Decimal('1000.00'), 'max_participants': 50, 'total_boxes': 100, 'duration_minutes': 240,
                'rules_description': 'Pick a number between 1 and 100. Lowest unique non-duplicate number wins.',
                'is_featured': True, 'views_count': 2100
            },
            {
                'title': 'MacBook Pro M3 - Secret Number Guess', 'product': p3, 'game_type': 'SECRET_NUMBER',
                'entry_fee': Decimal('1500.00'), 'max_participants': 30, 'total_boxes': 100, 'duration_minutes': 120,
                'rules_description': 'Backend generated a secret target number (1-500). Closest guess wins!',
                'is_recommended': True, 'views_count': 890
            },
            {
                'title': 'Sony Headphones - Precision Timer', 'product': p4, 'game_type': 'PRECISION_TIMER',
                'entry_fee': Decimal('200.00'), 'max_participants': 60, 'total_boxes': 100, 'duration_minutes': 90,
                'rules_description': 'Hit Start and click Stop as close as possible to 10.000 seconds. Smallest delta wins!',
                'views_count': 640
            },
            {
                'title': 'Rolex Submariner - Highest Unique Card', 'product': p5, 'game_type': 'HIGHEST_CARD',
                'entry_fee': Decimal('2500.00'), 'max_participants': 52, 'total_boxes': 52, 'duration_minutes': 300,
                'rules_description': 'Draw/pick a playing card. Duplicates are eliminated. Highest unique rank wins!',
                'is_featured': True, 'views_count': 3400
            }
        ]

        for gdata in games_list:
            Game.objects.get_or_create(
                title=gdata['title'],
                defaults={
                    'product': gdata['product'],
                    'seller': seller,
                    'game_type': gdata['game_type'],
                    'entry_fee': gdata['entry_fee'],
                    'max_participants': gdata['max_participants'],
                    'total_boxes': gdata.get('total_boxes', 100),
                    'duration_minutes': gdata['duration_minutes'],
                    'rules_description': gdata['rules_description'],
                    'question_prompt': gdata.get('question_prompt'),
                    'status': 'ACTIVE',
                    'is_featured': gdata.get('is_featured', False),
                    'is_recommended': gdata.get('is_recommended', False),
                    'views_count': gdata.get('views_count', 100)
                }
            )

        self.stdout.write(self.style.SUCCESS("Successfully seeded categories, products, and ALL 8 game challenges!"))
