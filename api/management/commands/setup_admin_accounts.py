from django.core.management.base import BaseCommand
from django.contrib.auth.models import User
from api.models import UserProfile, Wallet

class Command(BaseCommand):
    help = 'Seeds default local development admin accounts (bewnet and kun) safely.'

    def handle(self, *args, **kwargs):
        admin_accounts = [
            {'username': 'bewnet', 'email': 'bewnet@addisgigs.et', 'password': 'admin123'},
            {'username': 'kun', 'email': 'kun@addisgigs.et', 'password': 'admin123'},
        ]

        for acc in admin_accounts:
            user, created = User.objects.get_or_create(
                username=acc['username'],
                defaults={'email': acc['email'], 'is_staff': True, 'is_superuser': True}
            )
            if created or not user.check_password(acc['password']):
                user.set_password(acc['password'])
                user.email = acc['email']
                user.is_staff = True
                user.is_superuser = True
                user.save()

            # Ensure profile has ADMIN role
            profile, _ = UserProfile.objects.get_or_create(user=user)
            if profile.role != 'ADMIN':
                profile.role = 'ADMIN'
                profile.save()

            # Ensure wallet exists
            Wallet.objects.get_or_create(user=user)

            if created:
                self.stdout.write(self.style.SUCCESS(f"Successfully created admin account: {user.username}"))
            else:
                self.stdout.write(self.style.SUCCESS(f"Verified admin account: {user.username}"))
