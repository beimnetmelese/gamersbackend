import sys
from django.apps import AppConfig
from django.db.models.signals import post_migrate

def seed_admin_accounts_signal(sender, **kwargs):
    try:
        from django.core.management import call_command
        call_command('setup_admin_accounts')
    except Exception:
        pass

class ApiConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'api'

    def ready(self):
        post_migrate.connect(seed_admin_accounts_signal, sender=self)
        if 'runserver' in sys.argv or 'migrate' in sys.argv:
            try:
                from django.core.management import call_command
                call_command('setup_admin_accounts')
            except Exception:
                pass

