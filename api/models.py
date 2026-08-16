from django.db import models
from django.contrib.auth.models import User
from django.utils import timezone

class UserProfile(models.Model):
    ROLE_CHOICES = [
        ('USER', 'User'),
        ('SELLER', 'Seller'),
        ('ADMIN', 'Admin'),
    ]
    STATUS_CHOICES = [
        ('ACTIVE', 'Active'),
        ('SUSPENDED', 'Suspended'),
        ('BANNED', 'Banned'),
    ]
    
    user = models.OneToOneField(User, on_delete=models.CASCADE, related_name='profile')
    role = models.CharField(max_length=20, choices=ROLE_CHOICES, default='USER')
    account_status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='ACTIVE')
    bio = models.TextField(blank=True, default='')
    avatar_url = models.URLField(blank=True, default='')
    telegram_username = models.CharField(max_length=100, blank=True, default='')
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"{self.user.username} ({self.role})"


class SellerProfile(models.Model):
    STATUS_CHOICES = [
        ('PENDING', 'Pending Verification'),
        ('VERIFIED', 'Verified'),
        ('SUSPENDED', 'Suspended'),
        ('REJECTED', 'Rejected'),
    ]

    user = models.OneToOneField(User, on_delete=models.CASCADE, related_name='seller_profile')
    business_name = models.CharField(max_length=150)
    description = models.TextField(blank=True, default='')
    phone_number = models.CharField(max_length=30)
    address = models.CharField(max_length=255)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='PENDING')
    created_at = models.DateTimeField(auto_now_add=True)
    verified_at = models.DateTimeField(null=True, blank=True)

    def __str__(self):
        return f"{self.business_name} [{self.status}]"


class Product(models.Model):
    APPROVAL_CHOICES = [
        ('PENDING', 'Pending'),
        ('APPROVED', 'Approved'),
        ('REJECTED', 'Rejected'),
    ]
    CONDITION_CHOICES = [
        ('NEW', 'Brand New'),
        ('REFURBISHED', 'Refurbished'),
        ('USED', 'Used - Excellent'),
    ]

    seller = models.ForeignKey(SellerProfile, on_delete=models.CASCADE, related_name='products')
    title = models.CharField(max_length=200)
    category = models.CharField(max_length=100)
    description = models.TextField()
    image_url = models.URLField(blank=True, default='')
    condition = models.CharField(max_length=30, choices=CONDITION_CHOICES, default='NEW')
    estimated_value = models.DecimalField(max_digits=12, decimal_places=2)
    location = models.CharField(max_length=100, default='Addis Ababa')
    approval_status = models.CharField(max_length=20, choices=APPROVAL_CHOICES, default='PENDING')
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return self.title


class Game(models.Model):
    GAME_TYPES = [
        ('TREASURE_BOX', 'Treasure Box'),
        ('LOWEST_UNIQUE', 'Lowest Unique Number'),
        ('HIGHEST_CARD', 'Highest Unique Card'),
        ('SECRET_NUMBER', 'Secret Number'),
        ('PREDICTION', 'Prediction Challenge'),
        ('PRECISION_TIMER', 'Precision Timer'),
    ]
    STATUS_CHOICES = [
        ('DRAFT', 'Draft'),
        ('PENDING_APPROVAL', 'Pending Approval'),
        ('APPROVED', 'Approved'),
        ('ACTIVE', 'Active'),
        ('COMPLETED', 'Completed'),
        ('CANCELLED', 'Cancelled'),
        ('REFUNDED', 'Refunded'),
    ]

    product = models.ForeignKey(Product, on_delete=models.CASCADE, related_name='games')
    seller = models.ForeignKey(SellerProfile, on_delete=models.CASCADE, related_name='games')
    title = models.CharField(max_length=200)
    game_type = models.CharField(max_length=30, choices=GAME_TYPES)
    entry_fee = models.DecimalField(max_digits=10, decimal_places=2)
    max_participants = models.IntegerField(default=100)
    total_boxes = models.IntegerField(default=100, help_text="Total available boxes for Treasure Box")
    duration_minutes = models.IntegerField(default=120)
    
    # Target answer/secret value (stored securely on backend)
    secret_target = models.CharField(max_length=255, blank=True, null=True, help_text="Secret target number, prediction answer, or target time in ms")
    
    rules_description = models.TextField()
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='DRAFT')
    
    created_at = models.DateTimeField(auto_now_add=True)
    start_time = models.DateTimeField(null=True, blank=True)
    end_time = models.DateTimeField(null=True, blank=True)

    def __str__(self):
        return f"{self.title} ({self.get_game_type_display()}) - {self.status}"


class GameParticipant(models.Model):
    game = models.ForeignKey(Game, on_delete=models.CASCADE, related_name='participants')
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='game_entries')
    
    # Participant selections depending on game type
    selected_box = models.IntegerField(null=True, blank=True)
    selected_number = models.IntegerField(null=True, blank=True)
    selected_card = models.CharField(max_length=20, null=True, blank=True)
    prediction_answer = models.FloatField(null=True, blank=True)
    timer_delta_ms = models.IntegerField(null=True, blank=True)
    
    joined_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        # Prevent double entry per user if restricted, or box duplication for Treasure Box
        unique_together = [('game', 'user'), ('game', 'selected_box')]

    def __str__(self):
        return f"{self.user.username} in {self.game.title}"


class GameResult(models.Model):
    game = models.OneToOneField(Game, on_delete=models.CASCADE, related_name='result')
    winner = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, related_name='wins')
    winning_value = models.CharField(max_length=255, blank=True, default='')
    total_participants = models.IntegerField(default=0)
    calculated_at = models.DateTimeField(auto_now_add=True)
    resolution_notes = models.TextField(blank=True, default='')

    def __str__(self):
        return f"Result for {self.game.title}: Winner is {self.winner.username if self.winner else 'None'}"


class Wallet(models.Model):
    user = models.OneToOneField(User, on_delete=models.CASCADE, related_name='wallet')
    balance = models.DecimalField(max_digits=12, decimal_places=2, default=0.00)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"{self.user.username}'s Wallet: {self.balance} ETB"


class WalletTransaction(models.Model):
    TYPE_CHOICES = [
        ('DEPOSIT', 'Deposit'),
        ('GAME_ENTRY', 'Game Entry Fee'),
        ('REFUND', 'Refund'),
        ('WITHDRAWAL', 'Withdrawal'),
        ('REWARD', 'Reward / Prize'),
    ]
    
    wallet = models.ForeignKey(Wallet, on_delete=models.CASCADE, related_name='transactions')
    transaction_type = models.CharField(max_length=20, choices=TYPE_CHOICES)
    amount = models.DecimalField(max_digits=12, decimal_places=2)
    reference_id = models.CharField(max_length=100, blank=True, default='')
    note = models.CharField(max_length=255, blank=True, default='')
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"{self.transaction_type} {self.amount} ETB for {self.wallet.user.username}"


class PaymentSubmission(models.Model):
    STATUS_CHOICES = [
        ('PENDING', 'Pending Verification'),
        ('APPROVED', 'Approved'),
        ('REJECTED', 'Rejected'),
        ('REFUNDED', 'Refunded'),
    ]

    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='payment_submissions')
    payment_method = models.CharField(max_length=50) # Telebirr, CBE Birr, Bank Transfer
    transaction_id = models.CharField(max_length=100, unique=True)
    amount = models.DecimalField(max_digits=12, decimal_places=2)
    proof_image = models.ImageField(upload_to='payment_proofs/', null=True, blank=True)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='PENDING')
    admin_note = models.TextField(blank=True, default='')
    submitted_at = models.DateTimeField(auto_now_add=True)
    reviewed_at = models.DateTimeField(null=True, blank=True)

    def __str__(self):
        return f"Tx {self.transaction_id} ({self.amount} ETB) - {self.status}"


class ProductDelivery(models.Model):
    STATUS_CHOICES = [
        ('PREPARING', 'Preparing Package'),
        ('SHIPPED', 'Shipped'),
        ('OUT_FOR_DELIVERY', 'Out for Delivery'),
        ('DELIVERED', 'Delivered'),
        ('CONFIRMED', 'Confirmed Received'),
    ]

    game_result = models.OneToOneField(GameResult, on_delete=models.CASCADE, related_name='delivery')
    winner = models.ForeignKey(User, on_delete=models.CASCADE, related_name='deliveries')
    seller = models.ForeignKey(SellerProfile, on_delete=models.CASCADE, related_name='deliveries')
    delivery_address = models.TextField()
    phone_number = models.CharField(max_length=30)
    tracking_code = models.CharField(max_length=100, blank=True, default='')
    status = models.CharField(max_length=25, choices=STATUS_CHOICES, default='PREPARING')
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"Delivery for {self.game_result.game.title} [{self.status}]"


class Notification(models.Model):
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='notifications')
    title = models.CharField(max_length=150)
    message = models.TextField()
    event_type = models.CharField(max_length=50, default='SYSTEM')
    is_read = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"Notification to {self.user.username}: {self.title}"


class AuditLog(models.Model):
    actor = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, related_name='audit_actions')
    action = models.CharField(max_length=100)
    target_model = models.CharField(max_length=100)
    details = models.TextField()
    timestamp = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"Audit: {self.action} by {self.actor.username if self.actor else 'System'}"
